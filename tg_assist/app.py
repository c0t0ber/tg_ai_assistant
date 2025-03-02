import logging
from typing import cast

import pydantic.dataclasses
import pydantic_settings
from google.generativeai import GenerativeModel  # type: ignore[attr-defined]
from pydantic import ConfigDict
from telethon import TelegramClient
from telethon.tl.custom import Dialog
from telethon.tl.custom import Message as TelegramMessage
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import DialogFilter, TypeChat, TypeInputPeer, TypeUser
from telethon.tl.types.messages import DialogFilters
from telethon.utils import get_peer_id
from tqdm.auto import tqdm

from tg_assist.utils.telegram_auth_helper import get_telegram_code_from_request

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4000  # Немного меньше фактического лимита Telegram (4096)


@pydantic.dataclasses.dataclass
class Message:
    message_content: str
    message_id: int
    message_link: str


@pydantic.dataclasses.dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class ParsedDialog:  # type: ignore[misc]
    chat_title: str
    unread_count: int
    unread_messages: list[Message]
    telegram_peer: TypeInputPeer


@pydantic.dataclasses.dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class Folder:  # type: ignore[misc]
    folder_id: int
    folder_title: str
    included_chats: list[TypeInputPeer]


def create_telegram_message_link(chat_entity: TypeChat | TypeUser, message_id: int) -> str:
    """
    Создает ссылку на сообщение в Telegram

    Args:
        chat_entity: Объект чата или пользователя
        message_id: ID сообщения

    Returns:
        Ссылка на сообщение
    """
    try:
        if hasattr(chat_entity, "username") and chat_entity.username:
            # Публичный чат с username
            return f"https://t.me/{chat_entity.username}/{message_id}"
        else:
            # Приватный чат
            raw_chat_id = get_peer_id(chat_entity)
            chat_id_string = str(raw_chat_id)

            if chat_id_string.startswith("-100"):
                chat_link_id = chat_id_string[4:]  # Убираем префикс -100
            elif chat_id_string.startswith("-"):
                chat_link_id = chat_id_string[1:]  # Убираем минус
            else:
                chat_link_id = chat_id_string

            return f"https://t.me/c/{chat_link_id}/{message_id}"
    except Exception as error:
        logger.error(f"Ошибка при создании ссылки: {error}")
        return f"Message ID: {message_id}"


def split_message_into_chunks(text: str, max_chunk_size: int) -> list[str]:
    """
    Разделяет длинное сообщение на части подходящего размера

    Args:
        text: Исходный текст для разделения
        max_chunk_size: Максимальный размер одной части

    Returns:
        Список частей сообщения
    """
    message_chunks = []
    current_chunk = ""

    # Разделяем по строкам
    text_lines = text.split("\n")

    for text_line in text_lines:
        # Если добавление этой строки превысит лимит, добавляем блок в список
        if len(current_chunk + text_line + "\n") > max_chunk_size:
            if current_chunk:  # Проверяем, что блок не пустой
                message_chunks.append(current_chunk)
                current_chunk = text_line + "\n"
            else:
                # Случай, когда одна строка больше максимальной длины
                # Разбиваем ее на части
                while len(text_line) > max_chunk_size:
                    message_chunks.append(text_line[:max_chunk_size])
                    text_line = text_line[max_chunk_size:]
                current_chunk = text_line + "\n"
        else:
            current_chunk += text_line + "\n"

    # Добавляем последний блок, если он не пустой
    if current_chunk:
        message_chunks.append(current_chunk)

    return message_chunks


def format_summary_message(summary_text: str) -> str:
    """
    Форматирует текст суммаризации в сообщение для отправки

    Args:
        summary_text: Исходный текст суммаризации

    Returns:
        Отформатированное сообщение
    """
    return f"""
# 📝 Сводка непрочитанных сообщений

{summary_text}

_Сгенерировано с помощью TG Assist_
"""


def process_telegram_message(
    telegram_message: TelegramMessage, chat_entity: TypeChat | TypeUser
) -> Message:
    """
    Обрабатывает сообщение из Telegram

    Args:
        telegram_message: Сообщение из Telegram
        chat_entity: Объект чата или пользователя

    Returns:
        Обработанное сообщение
    """
    message_link = create_telegram_message_link(chat_entity, telegram_message.id)
    return Message(telegram_message.text, telegram_message.id, message_link)


class TelegramSummarizer:
    def __init__(
        self,
        telegram_api_id: int,
        telegram_api_hash: str,
        user_phone_number: str,
        session_name: str,
    ):
        """
        Инициализация класса для суммаризации телеграм сообщений

        Args:
            telegram_api_id: API ID из Telegram Developer Tools
            telegram_api_hash: API Hash из Telegram Developer Tools
            user_phone_number: Номер телефона пользователя
            session_name: Имя для сохранения сессии Telegram
        """
        self.user_phone_number = user_phone_number
        self.client = TelegramClient(session_name, telegram_api_id, telegram_api_hash)
        self.model = GenerativeModel("gemini-2.0-flash-thinking-exp-01-21")
        self.summarization_prompt_template = """
Ты мой личный ассистент и суммаризируешь телеграм чаты. 
Я отправлю тебе сообщения из чатов и тебе нужно их прочитать и очень коротко рассказать что я пропустил по каждому чату, выделяя только главные мысли 
Так же не нужно давать пользователю id сообщения, а кидай ссылку на сообщение

Пиши так, исползуй как шаблон:
<оригинальный Тайтл канала> - если на один канал несколько сообщений, оставляй под одним тайтлом и все     
<Самая главноя суть сообщение, о чем оно, не больше 10 слов> - <ссылка на сообщение> 
<Суммаризация сообщения, желательно не больше 2х предложений>

Далее отправляю тебе сообщения
"""

    async def run(self, folder_id: int, result_output_tg_entity: str) -> None:
        """
        Основной метод для запуска процесса суммаризации

        Args:
            folder_id: ID папки в Telegram с непрочитанными сообщениями
            result_output_tg_entity: ID чата, куда отправить суммаризацию
        """
        await self.client.start(self.user_phone_number, code_callback=get_telegram_code_from_request)
        logger.info("Telegram клиент успешно запущен")

        output_chat_entity = await self.client.get_entity(result_output_tg_entity)
        logger.info(f"Получен объект чата для отправки: {output_chat_entity.id}")

        try:
            summary_text = await self._get_unread_chats(folder_id)
            await self._send_summary_to_telegram(summary_text, output_chat_entity)
        finally:
            await self.client.disconnect()
            logger.info("Telegram клиент отключён")

    async def _get_folders(self) -> dict[int, Folder]:
        """
        Получение папок (фильтров диалогов) Telegram

        Returns:
            Словарь папок с ID в качестве ключей
        """
        dialog_filters_response: DialogFilters = await self.client(GetDialogFiltersRequest())
        telegram_folders: dict[int, Folder] = {}

        for dialog_filter in dialog_filters_response.filters:
            if isinstance(dialog_filter, DialogFilter):
                telegram_folders[dialog_filter.id] = Folder(
                    dialog_filter.id, dialog_filter.title.text, dialog_filter.include_peers
                )

        return telegram_folders

    async def _parse_dialog(self, telegram_dialog: Dialog) -> ParsedDialog:
        """
        Обработка диалога для извлечения непрочитанных сообщений

        Args:
            telegram_dialog: Объект диалога из Telethon

        Returns:
            Обработанный диалог с извлеченными сообщениями
        """
        chat_title = telegram_dialog.title
        message_limit = telegram_dialog.unread_count
        logger.debug(f"Обработка чата: {chat_title}, непрочитанных сообщений: {message_limit}")

        parsed_messages = []

        if telegram_dialog.unread_count >= 20:  # Ограничение в 20 сообщений
            logger.warning(
                f"Слишком много непрочитанных сообщений ({telegram_dialog.unread_count}), для чата {chat_title}, ограничиваю до 20"
            )
            message_limit = 20

        messages = await self.client.get_messages(telegram_dialog.entity, limit=message_limit)
        chat_entity = await self.client.get_entity(telegram_dialog.entity)

        for telegram_message in messages:
            try:
                parsed_message = process_telegram_message(telegram_message, chat_entity)
            except Exception as error:
                logger.exception(f"Ошибка при обработке сообщения ({telegram_message.to_dict()}): {error}")
                continue
            parsed_messages.append(parsed_message)

        return ParsedDialog(chat_title, telegram_dialog.unread_count, parsed_messages, telegram_dialog.input_entity)

    async def _summarize_with_gemini(self, unread_dialogs: list[ParsedDialog]) -> str:
        """
        Суммаризация сообщений с помощью Google Gemini

        Args:
            unread_dialogs: Список непрочитанных диалогов для суммаризации

        Returns:
            Текст суммаризации
        """
        complete_prompt = self.summarization_prompt_template
        logger.debug(f"Отправка на суммаризацию {len(unread_dialogs)} диалогов")

        for dialog in unread_dialogs:
            formatted_messages = ""
            for message in dialog.unread_messages:
                formatted_messages += (
                    f"msg id {message.message_id}\n {message.message_content}\n link: {message.message_link}\n\n"
                )

            complete_prompt += f"""\n
{dialog.chat_title} ({dialog.unread_count} непрочитанных сообщений):
{formatted_messages}\n\n     
"""

        logger.info("Запрос к Gemini API для суммаризации сообщений")
        gemini_response = self.model.generate_content(complete_prompt)
        return cast(str, gemini_response.text)

    async def _get_unread_chats(self, folder_id: int) -> str:
        """
        Получение и суммаризация непрочитанных сообщений из указанной папки

        Args:
            folder_id: ID папки в Telegram

        Returns:
            Текст суммаризации

        Raises:
            ValueError: Если папка не найдена или нет непрочитанных сообщений
        """
        telegram_folders = await self._get_folders()
        logger.info(
            f"Получены папки: {[(folder.folder_title, folder.folder_id) for folder in telegram_folders.values()]}"
        )

        if folder_id not in telegram_folders:
            error_msg = f"Папка с ID {folder_id} не найдена"
            logger.error(error_msg)
            raise ValueError(error_msg)

        selected_folder = telegram_folders[folder_id]
        logger.info(f"Выбрана папка: {selected_folder.folder_title} (ID: {selected_folder.folder_id})")

        unread_dialogs: list[ParsedDialog] = []

        async for telegram_dialog in tqdm(self.client.iter_dialogs(), desc="Читаю ваши диалоги", unit="Диалог"):
            if telegram_dialog.input_entity in selected_folder.included_chats:
                if telegram_dialog.unread_count > 0:
                    processed_dialog = await self._parse_dialog(telegram_dialog)
                    unread_dialogs.append(processed_dialog)

        if not unread_dialogs:
            error_msg = f"Нет непрочитанных сообщений в папке {selected_folder.folder_title}"
            logger.warning(error_msg)
            raise ValueError(error_msg)

        logger.info(f"Найдено {len(unread_dialogs)} диалогов с непрочитанными сообщениями")

        return await self._summarize_with_gemini(unread_dialogs)

    async def _send_summary_to_telegram(self, summary_text: str, chat_id: TypeChat) -> None:
        """
        Отправка суммаризации в указанный чат с поддержкой больших сообщений

        Args:
            summary_text: Текст суммаризации
            chat_id: ID чата для отправки
        """

        complete_summary_message = format_summary_message(summary_text)

        # Проверяем длину сообщения
        if len(complete_summary_message) <= TELEGRAM_MESSAGE_LIMIT:
            # Если сообщение помещается в один блок, отправляем его целиком
            logger.info(f"Отправка суммаризации одним сообщением в чат {chat_id}")
            await self.client.send_message(
                chat_id,
                complete_summary_message,
            )
        else:
            # Если сообщение слишком большое, разбиваем его на части
            logger.info("Суммаризация превышает лимит сообщения, разбиваем на части")

            # Сначала отправляем заголовок
            await self.client.send_message(
                chat_id,
                "# 📝 Сводка непрочитанных сообщений",
            )

            # Разбиваем основной текст по строкам
            message_chunks = split_message_into_chunks(summary_text, TELEGRAM_MESSAGE_LIMIT)

            # Отправляем все блоки
            for chunk_index, message_chunk in enumerate(message_chunks):
                chunk_content = message_chunk

                # К последнему блоку добавляем подпись
                if chunk_index == len(message_chunks) - 1:
                    chunk_content += "\n_Сгенерировано с помощью TG Assist_"

                logger.debug(f"Отправка части {chunk_index + 1}/{len(message_chunks)}")
                await self.client.send_message(chat_id, chunk_content, parse_mode="md")

            logger.info(f"Отправлено {len(message_chunks)} сообщений в чат {chat_id}")


class Settings(pydantic_settings.BaseSettings):  # type: ignore[explicit-any]
    telegram_api_id: int
    telegram_api_hash: str
    telegram_phone: str
    google_api_key: str
    session_name: str
    folder_id: int
    result_output_tg_entity: str


async def start_tg_assist() -> None:
    """Основная функция для запуска процесса суммаризации"""
    config = Settings()

    telegram_summarizer = TelegramSummarizer(
        user_phone_number=config.telegram_phone,
        telegram_api_id=config.telegram_api_id,
        telegram_api_hash=config.telegram_api_hash,
        session_name=config.session_name,
    )

    await telegram_summarizer.run(folder_id=config.folder_id, result_output_tg_entity=config.result_output_tg_entity)
    logger.info("Суммаризация успешно завершена")

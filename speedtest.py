import argparse
import os
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

ATTEMPTS = int(os.getenv("SPEEDTEST_ATTEMPTS", "10"))
CHUNK_SIZE = int(os.getenv("SPEEDTEST_CHUNK_SIZE", str(64 * 1024)))
DOWNLOAD_TIMEOUT = float(os.getenv("SPEEDTEST_DOWNLOAD_TIMEOUT", "30.0"))
CONNECT_TIMEOUT = float(os.getenv("SPEEDTEST_CONNECT_TIMEOUT", "10.0"))
READ_TIMEOUT = float(os.getenv("SPEEDTEST_READ_TIMEOUT", "30.0"))
DEFAULT_URL = os.getenv("SPEEDTEST_URL", "https://proof.ovh.net/files/10Mb.dat")
BYTES_PER_MB = 1_000_000


class DownloadError(Exception):
    """Ошибка целостности или ограничения времени загрузки."""


class DownloadTimeoutError(DownloadError):
    """Превышен абсолютный лимит времени скачивания тела ответа."""


class IncompleteReadError(DownloadError):
    """Фактический размер тела ответа не совпал с Content-Length."""


@dataclass(frozen=True)
class AttemptResult:
    elapsed: float
    downloaded_bytes: int

    @property
    def megabytes(self) -> float:
        return bytes_to_mb(self.downloaded_bytes)

    @property
    def speed_mbps(self) -> float:
        return calculate_speed_mbps(self.downloaded_bytes, self.elapsed)


@dataclass(frozen=True)
class Statistics:
    successful_attempts: int
    average_time: float
    total_bytes: int
    total_time: float

    @property
    def total_mb(self) -> float:
        return bytes_to_mb(self.total_bytes)

    @property
    def speed_mbps(self) -> float:
        return calculate_speed_mbps(self.total_bytes, self.total_time)


def bytes_to_mb(byte_count: int) -> float:
    return byte_count / BYTES_PER_MB


def calculate_speed_mbps(total_bytes: int, total_time: float) -> float:
    if total_time <= 0:
        return 0.0
    return total_bytes * 8 / total_time / BYTES_PER_MB


def aggregate_statistics(
    results: Sequence[AttemptResult],
) -> Statistics | None:
    if not results:
        return None

    total_time = sum(result.elapsed for result in results)
    return Statistics(
        successful_attempts=len(results),
        average_time=total_time / len(results),
        total_bytes=sum(result.downloaded_bytes for result in results),
        total_time=total_time,
    )


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise argparse.ArgumentTypeError(
            "URL должен использовать схему http или https и содержать хост"
        )
    return value


def add_cache_buster(url: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_cb", uuid.uuid4().hex))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def get_content_length(response: requests.Response) -> int | None:
    value = response.headers.get("Content-Length")
    if value is None:
        return None

    try:
        content_length = int(value)
        if content_length < 0:
            raise ValueError
    except ValueError as exc:
        raise DownloadError(
            f"Некорректный заголовок Content-Length: {value!r}"
        ) from exc
    return content_length


def download_once(session: requests.Session, url: str) -> AttemptResult:
    request_url = add_cache_buster(url)

    with session.get(
        request_url,
        stream=True,
        headers={"Accept-Encoding": "identity"},
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        allow_redirects=False,
    ) as response:
        if 300 <= response.status_code < 400:
            raise DownloadError(
                f"Редиректы отключены: получен HTTP {response.status_code}"
            )
        response.raise_for_status()
        expected_bytes = get_content_length(response)

        content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
        if content_encoding and content_encoding != "identity":
            raise DownloadError(
                "Сервер проигнорировал Accept-Encoding: identity "
                f"и вернул Content-Encoding: {content_encoding}"
            )

        started_at = time.perf_counter()
        downloaded_bytes = 0

        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if time.perf_counter() - started_at > DOWNLOAD_TIMEOUT:
                raise DownloadTimeoutError(
                    f"Скачивание превысило {DOWNLOAD_TIMEOUT:.0f} секунд"
                )

            if chunk:
                downloaded_bytes += len(chunk)

        elapsed = time.perf_counter() - started_at

        if expected_bytes is not None and downloaded_bytes != expected_bytes:
            raise IncompleteReadError(
                f"Content-Length: {expected_bytes} байт, "
                f"фактически получено: {downloaded_bytes} байт"
            )

        return AttemptResult(elapsed, downloaded_bytes)


def print_report(statistics: Statistics) -> None:
    separator = "=" * 60
    print(f"\n{separator}")
    print("Результаты замера:")
    print(separator)
    print(f"Успешных запросов: {statistics.successful_attempts}/{ATTEMPTS}")
    print(f"Среднее время успешного запроса: {statistics.average_time:.3f} с")
    print(f"Общий объем скачанных данных: {statistics.total_mb:.3f} МБ")
    print(f"Итоговая средняя скорость: {statistics.speed_mbps:.3f} Мбит/с")
    print(separator)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Замер скорости скачивания файла по HTTP(S)."
    )
    parser.add_argument(
        "url",
        nargs="?",
        type=validate_url,
        help="URL файла; если не указан, используется SPEEDTEST_URL",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.url is None:
        try:
            url = validate_url(DEFAULT_URL)
        except argparse.ArgumentTypeError as exc:
            print(f"Некорректный SPEEDTEST_URL: {exc}", file=sys.stderr)
            return 2
        print(f"URL не указан. Тест будет выполнен для: {url}")
    else:
        url = args.url

    results: list[AttemptResult] = []
    print(f"{'Попытка':<10}{'Время, с':>12}{'Объем, МБ':>14}{'Скорость, Мбит/с':>20}")
    print("-" * 56)

    with requests.Session() as session:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                result = download_once(session, url)
            except (requests.RequestException, DownloadError) as exc:
                print(
                    f"Попытка {attempt}/{ATTEMPTS}: ошибка: {exc}",
                    file=sys.stderr,
                )
            else:
                results.append(result)
                print(
                    f"{attempt:>4}/{ATTEMPTS:<5}"
                    f"{result.elapsed:>12.3f}"
                    f"{result.megabytes:>14.3f}"
                    f"{result.speed_mbps:>20.3f}"
                )

    statistics = aggregate_statistics(results)
    if statistics is None:
        print(
            "Не удалось выполнить ни одной успешной попытки; метрики не рассчитаны.",
            file=sys.stderr,
        )
        return 1

    print_report(statistics)
    return 0


if __name__ == "__main__":
    sys.exit(main())

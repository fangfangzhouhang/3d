"""通过明确指定的 COM 口检查 NUCLEO 的 PING 或 STATUS。

不提供 PUMP 参数，也不会枚举或自动打开其他串口。未提供 ``--port`` 时只打印将要
发送的协议文本，便于硬件组在固件尚未完成时核对接口。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from microcleaning.control_system.stm32_protocol import (
    STM32ProtocolError,
    encode_ping,
    encode_status,
    parse_response,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查 NUCLEO-F401RE 的最小串口协议；不发送水泵或移动命令。"
    )
    parser.add_argument("--port", help="明确指定端口，如 COM5；省略时只预览消息")
    parser.add_argument("--baudrate", type=int, default=115200, help="默认 115200")
    parser.add_argument("--timeout", type=float, default=2.0, help="读取超时秒数，默认 2.0")
    parser.add_argument("--command", choices=("ping", "status"), default="ping")
    args = parser.parse_args(argv)

    if args.baudrate <= 0:
        parser.error("--baudrate 必须是正整数")
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")

    payload = encode_ping() if args.command == "ping" else encode_status()
    if not args.port:
        print("未提供 --port，本次不会打开真实串口。")
        print(f"将发送: {payload.decode('ascii').rstrip()}")
        return 0

    try:
        import serial
    except ImportError:
        print(
            "缺少 pyserial。确认需要实机探测后运行：\n"
            r".\.venv\Scripts\python.exe -m pip install -r requirements\control-serial.txt",
            file=sys.stderr,
        )
        return 2

    try:
        with serial.Serial(
            port=args.port,
            baudrate=args.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=args.timeout,
            write_timeout=args.timeout,
        ) as connection:
            connection.reset_input_buffer()
            connection.write(payload)
            connection.flush()
            response = connection.readline()
    except (serial.SerialException, OSError) as exc:
        print(f"SERIAL_CONNECTION_FAILED: {exc}", file=sys.stderr)
        return 3

    if not response:
        print("RESPONSE_TIMEOUT: 在超时时间内没有收到完整换行响应", file=sys.stderr)
        return 4

    try:
        parsed = parse_response(response)
    except STM32ProtocolError as exc:
        print(f"INVALID_RESPONSE: {exc}", file=sys.stderr)
        return 5

    print(json.dumps(asdict(parsed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

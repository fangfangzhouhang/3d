"""发送单条 Stage2 串口命令并打印固件回复（H2 联调工具）。

用于 Gate5 喷头偏移实测等需要手动逐条发命令的场景。
不扫描串口；不自动规划；只直发指定命令并回显回复。
支持 HELLO / SPEED N / MOVEXY N FWD|REV N FWD|REV / READXY / TO_NEEDLE / TO_SCOPE / STOP 等。
"""

from __future__ import annotations

import argparse
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发送单条 Stage2 命令并打印固件回复")
    parser.add_argument("--port", required=True, help="明确指定端口，如 COM5；禁止扫描")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--cmd", required=True, help="如 HELLO / SPEED 100 / MOVEXY 100 FWD 0 FWD / READXY / TO_NEEDLE / TO_SCOPE")
    parser.add_argument("--wait", type=float, default=0.3, help="命令后等待时间秒，默认 0.3")
    parser.add_argument("--timeout", type=float, default=1.5, help="单条回复读取超时秒，默认 1.5")
    args = parser.parse_args(argv)

    if args.baudrate <= 0:
        parser.error("--baudrate 必须是正整数")
    if args.wait < 0 or args.timeout <= 0:
        parser.error("--wait/--timeout 非法")

    try:
        import serial
    except ImportError:
        print("缺少 pyserial，请先安装 requirements/control-serial.txt", file=sys.stderr)
        return 2

    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=args.timeout,
            write_timeout=args.timeout,
        )
        ser.reset_input_buffer()
        ser.write((args.cmd + "\r\n").encode("ascii"))
        time.sleep(args.wait)
        replies: list[str] = []
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if ser.in_waiting:
                replies.append(ser.readline().decode("ascii", "replace").strip())
            else:
                time.sleep(0.05)
    except (serial.SerialException, OSError) as exc:
        print(f"SERIAL_CONNECTION_FAILED: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            ser.close()
        except Exception:
            pass

    if not replies:
        print("RESPONSE_TIMEOUT: 未收到完整换行回复")
        return 4
    print("\n".join(replies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

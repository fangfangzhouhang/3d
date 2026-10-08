"""实物闭环的同一个窗口：实时显微镜、第一次画面标注、中文说明和确认。

OpenCV 的文字不能写中文，所以说明放在这个窗口的文字区。确认、不确认和空格抓图
都在这里完成，不用回到 Cursor 终端。
"""

from __future__ import annotations

import base64
import time


class StationPanel:
    """主线程上的一个窗口。按钮只记下选择，真正的动作仍由原来的确认点决定。"""

    def __init__(self) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk()
        self.root.title("显微清洗")
        self.font = ("Microsoft YaHei UI", 12)
        self.small = ("Microsoft YaHei UI", 10)
        self._closed = False
        self._waiting = "keys"
        self._answer: bool | None = None
        self._dismissed = False
        self._keys: list[int] = []
        self._last_live = 0.0
        self.roster_locked = False
        self._right_source = ""
        self._live_photo = None
        self._roster_photo = None

        images = tk.Frame(self.root)
        images.pack(fill="both", expand=True, padx=8, pady=8)
        left = tk.Frame(images)
        right = tk.Frame(images)
        left.pack(side="left", padx=4)
        right.pack(side="left", padx=4)
        tk.Label(left, text="实时显微镜", font=self.font).pack()
        tk.Label(right, text="第一次画面的标注", font=self.font).pack()
        self.live_label = tk.Label(left, text="正在打开相机", font=self.small, width=42, height=12)
        self.roster_label = tk.Label(right, text="抓到第一帧之后，标注会留在这里", font=self.small, width=42, height=12)
        self.live_label.pack()
        self.roster_label.pack()

        self.status = tk.Label(self.root, text="正在准备", font=self.font, anchor="w")
        self.status.pack(fill="x", padx=8)
        self.log = tk.Text(self.root, height=9, wrap="word", font=self.font, takefocus=0)
        self.log.pack(fill="both", expand=True, padx=8, pady=4)
        self.log.configure(state="disabled")

        bar = tk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=8)
        self.yes_button = tk.Button(bar, text="确认（Y）", font=self.font, width=14, command=lambda: self._decide(True))
        self.no_button = tk.Button(bar, text="不确认（N）", font=self.font, width=14, command=lambda: self._decide(False))
        self.yes_button.pack(side="left", padx=4)
        self.no_button.pack(side="left", padx=4)
        tk.Label(bar, text="抓图按空格，结束按 Q。不用回到 Cursor 终端。", font=self.small).pack(side="left", padx=8)
        self._set_buttons()
        self.root.bind_all("<Key>", self._on_key)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._focus()
        self._pump()

    def write(self, message: str, *, echo: bool = True) -> None:
        text = str(message).rstrip()
        if echo:
            print(text, flush=True)
        if self._closed or self.root is None:
            return
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self._pump()

    def set_status(self, text: str) -> None:
        if self._closed or self.root is None:
            return
        self.status.configure(text=text)
        self._set_buttons()
        self._pump()

    def show_frame(self, name: str, image) -> None:
        if self._closed or image is None:
            return
        title = str(name).lower()
        if "path" in title:
            if self.roster_locked:
                self._pump()
                return
            self._set_photo(self.roster_label, image, "_roster_photo")
            self._right_source = "path"
        else:
            now = time.monotonic()
            if now - self._last_live < 0.08:
                self._pump()
                return
            self._last_live = now
            self._set_photo(self.live_label, image, "_live_photo")
        self._pump()

    def show_roster(self, image) -> None:
        self.roster_locked = True
        self._right_source = "roster"
        self._set_photo(self.roster_label, image, "_roster_photo")
        self.set_status("右边是第一次画面的标注。这次只处理这些污渍。")
        self._pump()

    def ask(self, prompt: str, refresh=None) -> bool:
        self.write(prompt)
        self.write("确认或 Y 批准。不确认或 N 不批准。不用回到 Cursor 终端。")
        self._waiting = "yesno"
        self._answer = None
        self.set_status("现在在这个窗口确认。确认或 Y 才批准。不确认或 N 不批准。")
        self._focus()
        while self._answer is None and not self._closed:
            if refresh is not None:
                refresh()
            else:
                self._pump()
            if self._answer is None and not self._closed:
                time.sleep(0.02)
        self._waiting = "keys"
        self.set_status("显微镜画面")
        return self._answer is True

    def wait_key(self, delay_ms: int) -> int:
        if self._keys:
            return self._keys.pop(0)
        if self._closed:
            return ord("q")
        deadline = time.monotonic() + max(int(delay_ms), 1) / 1000.0
        while time.monotonic() < deadline:
            self._pump()
            if self._keys:
                return self._keys.pop(0)
            time.sleep(0.01)
        return -1

    def wait_dismiss(self, seconds: float = 8) -> None:
        self._waiting = "dismiss"
        self._dismissed = False
        self.set_status("这一次结束。点窗口关闭或按 Q。")
        self._focus()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._closed and not self._dismissed:
            self._pump()
            if self._keys:
                key = self._keys.pop(0)
                if key in {ord("q"), ord("Q"), 27}:
                    break
            time.sleep(0.02)
        self.close()

    def close(self) -> None:
        root, self.root = self.root, None
        self._closed = True
        if root is not None:
            try:
                root.destroy()
            except self.tk.TclError:
                pass

    def _decide(self, accepted: bool) -> None:
        if self._waiting != "yesno" or self._answer is not None:
            return
        self._answer = bool(accepted)

    def _on_key(self, event):
        keysym = (event.keysym or "").lower()
        if self._waiting == "yesno":
            if keysym == "y":
                self._decide(True)
            elif keysym == "n":
                self._decide(False)
            return "break"
        if keysym == "space":
            self._keys.append(32)
        elif keysym == "q":
            self._keys.append(ord("q"))
        elif keysym == "escape":
            self._keys.append(27)
            if self._waiting == "dismiss":
                self._dismissed = True
        return "break"

    def _on_close(self) -> None:
        self._closed = True
        self._dismissed = True
        if self._answer is None:
            self._answer = False
        self._keys.append(ord("q"))
        if self.root is not None:
            try:
                self.root.withdraw()
            except self.tk.TclError:
                pass

    def _set_buttons(self) -> None:
        state = "normal" if self._waiting == "yesno" else "disabled"
        self.yes_button.configure(state=state)
        self.no_button.configure(state=state)

    def _focus(self) -> None:
        if self._closed or self.root is None:
            return
        try:
            self.root.lift()
            self.root.focus_force()
        except self.tk.TclError:
            self._closed = True

    def _pump(self) -> None:
        if self._closed or self.root is None:
            return
        try:
            self.root.update_idletasks()
            self.root.update()
        except self.tk.TclError:
            self._closed = True

    def _set_photo(self, label, image, attr: str) -> None:
        import cv2

        fitted = _fit(image)
        okay, encoded = cv2.imencode(".png", fitted)
        if not okay:
            return
        payload = base64.b64encode(encoded.tobytes()).decode("ascii")
        try:
            photo = self.tk.PhotoImage(data=payload)
        except self.tk.TclError:
            rgb = cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            photo = self.tk.PhotoImage(width=width, height=height)
            photo.put(" ".join(
                "{" + " ".join(f"#{red:02x}{green:02x}{blue:02x}" for red, green, blue in row) + "}"
                for row in rgb
            ))
        setattr(self, attr, photo)
        label.configure(image=photo, text="", width=0, height=0)


def _fit(image, max_w: int = 520, max_h: int = 390):
    import cv2

    height, width = image.shape[:2]
    scale = min(max_w / max(width, 1), max_h / max(height, 1), 1.0)
    if scale == 1:
        return image
    return cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))))

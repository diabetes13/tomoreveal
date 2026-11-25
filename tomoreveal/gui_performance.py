"""Help ▸ Performance (3.6): what draws the pictures, memory, the graphics card's load, and where a frame's time goes."""
import threading

from PySide6 import QtCore as QtC, QtWidgets as QtW

from . import diagnose


class PerformanceDialog(QtW.QDialog):
    def __init__(self, win):
        super().__init__(win); self.win = win
        self.setWindowTitle("Performance"); self.resize(560, 420)
        v = QtW.QVBoxLayout(self)
        self.text = QtW.QLabel(""); self.text.setWordWrap(True); self.text.setTextFormat(QtC.Qt.RichText)
        self.text.setTextInteractionFlags(QtC.Qt.TextSelectableByMouse); v.addWidget(self.text)
        self.bars = QtW.QLabel(""); self.bars.setTextFormat(QtC.Qt.RichText); v.addWidget(self.bars, 1)
        row = QtW.QHBoxLayout()
        self.measure_btn = QtW.QPushButton("Time one movie frame")
        self.measure_btn.setToolTip("Draws the frame shown in the preview at the movie's size and shows where the time goes")
        self.measure_btn.clicked.connect(self.measure); row.addWidget(self.measure_btn); row.addStretch(1)
        close = QtW.QPushButton("Close"); close.setToolTip("Close this window"); close.clicked.connect(self.close); row.addWidget(close)
        v.addLayout(row)
        self._gpu = None; self._busy = False
        self.timer = QtC.QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self._poll)
        self.refresh()

    def showEvent(self, e):
        super().showEvent(e); self.timer.start()

    def hideEvent(self, e):
        super().hideEvent(e); self.timer.stop()  # nvidia-smi is never asked while the window is closed

    def _poll(self):
        if self._busy:
            return
        self._busy = True

        def run():
            self._gpu = diagnose.gpu_load(); self._busy = False
        threading.Thread(target=run, daemon=True).start()  # in a thread: nvidia-smi may take a moment
        self.refresh()

    def refresh(self):
        w = self.win; st = w.st; ren = st.ren
        rs = diagnose.renderer_string(ren.pl.ren_win) if ren is not None else ""
        cls = type(ren.pl.ren_win).__name__ if ren is not None else ""
        lines = [f"<b>Drawing with:</b> {diagnose.in_plain_words(rs, cls)}"]
        why = getattr(w, "software_reason", None)
        if why:
            lines.append(f"<span style='color:#c0392b'>Switched to software rendering in this session: {why}</span>")
        if self._gpu:
            a, b, c = self._gpu
            lines.append(f"<b>Graphics card:</b> {a:.0f} % busy, {b / 1024:.1f} of {c / 1024:.1f} GB memory used")
        mem = diagnose.data_memory(st.ds)
        if mem:
            lines.append("<b>Memory:</b> " + ", ".join(f"{k} {v / 1e9:.2f} GB" for k, v in mem.items()))
            n = diagnose.triangles(st.ds); lines.append(f"<b>Triangles drawn:</b> {n:,}")
            if n > 10e6:  # 3.6: each pass of the graphics card costs about 0.25 s per 28 million triangles
                big = sorted(((m.n_cells, st.ds.classes[k]["name"]) for k, m in st.ds.meshes.items() if m is not None), reverse=True)[:3]
                lines.append("Many triangles make drawing slower. For the largest structures (" + ", ".join(nm for _, nm in big)
                             + ") choose Structures tab ▸ ⚙ ▸ Detail ▸ half (about 4 times fewer), or Data tab ▸ Size ▸ half.")
        lines.append("<b>Video encoder:</b> processor (x264); encoding takes about 5 ms per HD frame")
        self.text.setText("<br>".join(lines))

    def measure(self):
        w = self.win
        if w.spec is None or w.st.ren is None:
            return
        from .look import look_settings
        spec = w.spec_for(); plan, i = w.st.plan_frame(spec, {"frame": w.frame})
        size = tuple(spec.get("output", {}).get("size", [1920, 1080]))
        QtW.QApplication.setOverrideCursor(QtC.Qt.WaitCursor)
        try:
            total, steps = diagnose.frame_steps(w.st.ren, plan.frames[i], spec, size, aa=look_settings(spec)["aa"]["movie"])
        finally:
            QtW.QApplication.restoreOverrideCursor()
        w.do_preview()  # the preview's own size again
        mx = max([s for _, s in steps] + [1e-9])
        rows = [f"<b>One {size[0]} × {size[1]} frame: {total:.2f} s</b> (about {1 / max(total, 1e-6):.1f} frames per second)<table>"]
        for name, sec in steps:
            bar = "█" * max(0, int(round(24 * sec / mx)))
            rows.append(f"<tr><td>{name}</td><td align='right'>&nbsp;{sec:.3f} s&nbsp;</td><td style='color:#4a90d9'>{bar}</td></tr>")
        self.bars.setText("".join(rows) + "</table>")
        self.refresh()

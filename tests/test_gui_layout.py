"""Layout polish tests (offscreen): minimum size, unclipped controls, native spin boxes."""
import unittest

from test_gui_shell import GuiCase, HAVE_QT, close, hex_rgb, rgb

if HAVE_QT:
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QLineEdit,
                                   QProgressBar, QPushButton, QRadioButton, QSpinBox,
                                   QStyle, QStyleOptionSpinBox)

    FORM_TYPES = (QSpinBox, QComboBox, QCheckBox, QRadioButton, QLineEdit, QPushButton)
    RUN_TYPES = (QPushButton, QProgressBar)


from platform_skips import font_metrics  # noqa: E402

class LayoutTests(GuiCase):
    def settled(self):
        w = self.window()
        w.resize(800, 640)
        for _ in range(3):
            QApplication.processEvents()
        if w.layout() is not None:
            w.layout().activate()
        return w

    def clipped(self, root, types):
        bad = []
        for c in root.findChildren(types[0]) if len(types) == 1 else \
                [c for t in types for c in root.findChildren(t)]:
            if c.objectName().startswith("qt_") or not c.isVisible():
                continue
            if c.height() < c.sizeHint().height() or c.width() < c.minimumSizeHint().width():
                bad.append((type(c).__name__, c.objectName(), c.size().toTuple(),
                            c.sizeHint().toTuple()))
        return bad

    def test_minimum_window_size_is_800x640(self):
        m = self.window().minimumSize()
        self.assertEqual((m.width(), m.height()), (800, 640))

    @font_metrics

    def test_layout_minimum_hint_fits_inside_the_window_minimum(self):
        h = self.window().minimumSizeHint()
        self.assertLessEqual(h.width(), 800)
        self.assertLessEqual(h.height(), 640)

    def test_form_controls_are_not_clipped_at_minimum_size(self):
        w = self.settled()
        self.assertEqual(self.clipped(w.search_form, FORM_TYPES), [])

    @font_metrics

    def test_run_panel_controls_are_not_clipped_at_minimum_size(self):
        w = self.window()
        w.download_stack.setCurrentIndex(1)
        w.resize(800, 640)
        for _ in range(3):
            QApplication.processEvents()
        if w.layout() is not None:
            w.layout().activate()
        self.assertEqual(self.clipped(w.run_panel, RUN_TYPES), [])

    def test_stylesheet_does_not_style_spin_boxes_or_combo_boxes(self):
        from rule34_downloader.gui import theme
        sheet = theme.stylesheet()
        self.assertNotIn("QSpinBox", sheet)
        self.assertNotIn("QComboBox", sheet)

    def test_spin_box_has_up_and_down_button_areas(self):
        spin = self.settled().search_form.findChild(QSpinBox, "min_score_spin")
        self.assertIsNotNone(spin)
        opt = QStyleOptionSpinBox()
        opt.initFrom(spin)
        opt.rect = spin.rect()
        for sc in (QStyle.SC_SpinBoxUp, QStyle.SC_SpinBoxDown):
            r = spin.style().subControlRect(QStyle.CC_SpinBox, opt, sc, spin)
            self.assertGreaterEqual(r.width(), 12, sc)
            self.assertGreaterEqual(r.height(), 8, sc)

    def test_spin_box_field_pixel_is_white(self):
        from rule34_downloader.gui import theme
        w = self.settled()
        spin = w.search_form.findChild(QSpinBox, "count_spin")
        self.assertIsNotNone(spin)
        img = spin.grab().toImage()
        self.assertTrue(close(rgb(img, 4, img.height() // 2), hex_rgb(theme.WHITE)))

    def test_initial_window_size_is_at_least_the_minimum(self):
        s = self.window().size()
        self.assertGreaterEqual(s.width(), 800)
        self.assertGreaterEqual(s.height(), 640)


if __name__ == "__main__":
    unittest.main()

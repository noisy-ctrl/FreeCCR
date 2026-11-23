"""Shorter capture names and a frame list that doesn't scroll sideways
(spec/capture-short-names.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import capture_log as cl  # noqa: E402


def test_new_names_are_short():
    assert cl.shot_stem("R0025", "35mm", "Colorplus", 4, "red") == "R0025_135_Colorplus_04_1R"
    assert cl.shot_stem("R0025", "35mm", "Colorplus", 4, "green").endswith("_04_2G")
    assert cl.shot_stem("R0025", "35mm", "Colorplus", 4, "blue").endswith("_04_3B")
    assert cl.shot_stem("R0026", "120", "HP5", 3, "bw") == "R0026_120_HP5_03_BW"
    assert cl.shot_stem("R0026", "645", "", 12, "white") == "R0026_645_12_W"
    assert cl.shot_stem("R0026", "6x7", "", 104, "ir") == "R0026_6x7_104_4IR"
    assert cl.format_tag("35mm") == "135" and cl.format_tag("645") == "645"
    # still sort red, green, blue, IR by name
    names = [cl.shot_stem("R1", "", "", 7, s) for s in ("ir", "blue", "red", "green")]
    assert sorted(names) == [cl.shot_stem("R1", "", "", 7, s)
                             for s in ("red", "green", "blue", "ir")]


def test_old_and_new_names_both_read():
    for name, want in (("R0025_135_Colorplus_04_1R.raf", ("r0025_135_colorplus", 4, "red")),
                       ("R0025_35mm_Colorplus_004_1RED.raf", ("r0025_35mm_colorplus", 4, "red")),
                       ("R0025_35mm_Colorplus_004_GREEN_1.raf", ("r0025_35mm_colorplus", 4, "green")),
                       ("R0026_120_HP5_03_BW.raf", ("r0026_120_hp5", 3, "bw")),
                       ("R0026_645_12_W.raf", ("r0026_645", 12, "white")),
                       ("R0026_645_012_WHITE.raf", ("r0026_645", 12, "white")),
                       ("R0026_645_12_4IR.raf", ("r0026_645", 12, "ir"))):
        assert cl.name_slot(name) == want, name
    assert cl.name_slot("DSCF0001.RAF") is None and cl.name_slot("R1_04_9X.raf") is None
    assert cl.slot_order("R1_04_3B_2.raf") == ("r1_04", 2, 2)
    assert cl.slot_order("R1_004_1RED.raf") == ("r1_004", 0, 0)
    assert cl.slot_order("R1_04_W.raf") is None
    assert cl.is_ir_file("/x/R1_04_4IR.raf") and cl.is_ir_file("/x/R1_004_4IR.raf")
    assert cl.shot_key("/x/R1_04_2G.raf")[1] == "r1_04"


def test_next_shot_counts_old_and_new_names(tmp_path):
    for n in ("R0025_35mm_Colorplus_004_1RED.raf", "R0025_135_Colorplus_07_3B.raf",
              "R0024_135_X_30_1R.raf"):
        (tmp_path / n).write_bytes(b"x")
    assert cl.next_shot(str(tmp_path), "R0025") == 8


def test_an_old_log_entry_keeps_its_old_named_files():
    """A log written before the change: format "35mm" in the entry, files named
    with 35mm. Its files must not be dropped as another shot's."""
    e = {"roll": "R0025", "format": "35mm", "film": "Colorplus", "shot": 4,
         "scan_type": "trichrome",
         "files": {"red": "R0025_35mm_Colorplus_004_1RED.raf",
                   "green": "R0025_35mm_Colorplus_004_2GREEN.raf",
                   "blue": "R0025_35mm_Colorplus_004_3BLUE.raf"}}
    assert cl.entry_files(e) == e["files"]
    new = dict(e, files={"red": "R0025_135_Colorplus_04_1R.raf",
                         "green": "R0025_135_Colorplus_05_2G.raf"})     # the next shot's
    assert cl.entry_files(new) == {"red": "R0025_135_Colorplus_04_1R.raf"}


def test_list_labels():
    L = cl.list_label
    assert L("R0025_35mm_Colorplus_004_1RED_RGB.raf") == "R0025_135_Colorplus_04"
    assert L("R0025_135_Colorplus_04_1R_RGB.raf") == "R0025_135_Colorplus_04"
    assert L("R0025_135_Colorplus_04_1R_RGB_2.raf") == "R0025_135_Colorplus_04_2"
    assert L("R0026_120_HP5_003_BW.raf") == "R0026_120_HP5_03_BW"
    assert L("R0026_645_012_WHITE.RAF") == "R0026_645_12_W"
    assert L("R0026_645_012_1RED_1.RAF") == "R0026_645_12_1R_1"
    assert L("DSCF0001.RAF") == "DSCF0001.RAF"            # other names as they are
    assert L("scan_s2.ARW") == "scan_s2.ARW"
    assert L("") == ""


def test_the_frame_list_has_short_labels_small_text_and_no_sideways_scroll(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui import theme
    from widgets.thumbnail_list import ThumbnailList

    class Img:
        def __init__(self, name):
            self.display_name = name
            self.file_path = "/x/" + name

    tl = ThumbnailList.__new__(ThumbnailList)
    lab, full = ThumbnailList._item_label(Img("R0025_35mm_Colorplus_004_1RED_RGB.raf"))
    assert lab == "R0025_135_Colorplus_04" and full == "R0025_35mm_Colorplus_004_1RED_RGB.raf"
    assert ThumbnailList._item_label(None) == ("", "")
    del tl
    import inspect
    src = inspect.getsource(ThumbnailList.init_ui)
    assert "ScrollBarAlwaysOff" in src and "ElideMiddle" in src and "FS_CAPTION" in src
    assert theme.FS_CAPTION <= 11
    assert ThumbnailList.LIST_GRID_W + 2 + 15 <= 196      # frame + a scroll bar fit

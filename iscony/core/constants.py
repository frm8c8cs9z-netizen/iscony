from reportlab.lib.units import mm


def _xy_mm(x, y):
    """PDF左下原点の座標をmmで指定し、ReportLab用のpointへ変換する。"""
    return (
        x * mm,
        y * mm,
    )


def _length_mm(value):
    """幅や高さをmmで指定し、ReportLab用のpointへ変換する。"""
    return value * mm


SCORE_SHEET_POSITION_PATTERNS = {
    # ---------------------------------
    # 標準採点票(黒)
    # ---------------------------------
    "standard": {
        "category": _xy_mm(44.1, 256.8),
        "court": _xy_mm(82.9, 256.8),
        "round": _xy_mm(44.1, 243.4),
        "pair1_code": _xy_mm(33.5, 231.1),
        "pair2_code": _xy_mm(130.5, 231.1),
        "pair1_org": _xy_mm(56.4, 225.8),
        "pair2_org": _xy_mm(155.2, 225.8),
        "pair1_player1": _xy_mm(61.7, 217.7),
        "pair1_player2": _xy_mm(61.7, 203.6),
        "pair2_player1": _xy_mm(158.8, 217.7),
        "pair2_player2": _xy_mm(158.8, 203.6),
        "margin_memo": _xy_mm(148.2, 266.3),
        "match_key": _xy_mm(149.0, 284.0),
        "match_key_value": _xy_mm(149.0, 278.0),
        "qr": _xy_mm(165.0, 271.0),
        "qr_size": _length_mm(20),
        "org_width": _length_mm(31.8),
        "org_height": _length_mm(14.1),
    },

    # ---------------------------------
    # 標準採点票（青）
    # ---------------------------------
    "ptrn1": {
        "category": _xy_mm(44.1, 259.3),
        "court": _xy_mm(82.9, 259.3),
        "round": _xy_mm(44.1, 245.9),
        "pair1_code": _xy_mm(33.5, 233.5),
        "pair2_code": _xy_mm(130.5, 233.5),
        "pair1_org": _xy_mm(56.4, 228.2),
        "pair2_org": _xy_mm(155.2, 228.2),
        "pair1_player1": _xy_mm(61.7, 220.1),
        "pair1_player2": _xy_mm(61.7, 206.0),
        "pair2_player1": _xy_mm(158.8, 220.1),
        "pair2_player2": _xy_mm(158.8, 206.0),
        "margin_memo": _xy_mm(148.2, 268.8),
        "match_key": _xy_mm(149.0, 286.5),
        "match_key_value": _xy_mm(149.0, 280.5),
        "qr": _xy_mm(165.0, 273.5),
        "qr_size": _length_mm(20),
        "org_width": _length_mm(31.8),
        "org_height": _length_mm(14.1),
    },

    # ---------------------------------
    # プレプリント用
    # ---------------------------------
    "ptrn2": {
        "category": _xy_mm(44.1, 256.8),
        "court": _xy_mm(82.9, 256.8),
        "round": _xy_mm(44.1, 243.4),
        "pair1_code": _xy_mm(33.5, 231.1),
        "pair2_code": _xy_mm(130.5, 231.1),
        "pair1_org": _xy_mm(56.4, 225.8),
        "pair2_org": _xy_mm(155.2, 225.8),
        "pair1_player1": _xy_mm(61.7, 217.7),
        "pair1_player2": _xy_mm(61.7, 203.6),
        "pair2_player1": _xy_mm(158.8, 217.7),
        "pair2_player2": _xy_mm(158.8, 203.6),
        "margin_memo": _xy_mm(148.2, 266.3),
        "match_key": _xy_mm(149.0, 284.0),
        "match_key_value": _xy_mm(149.0, 278.0),
        "qr": _xy_mm(165.0, 271.0),
        "qr_size": _length_mm(20),
        "org_width": _length_mm(31.8),
        "org_height": _length_mm(14.1),
    },
}

"""Tournament SVG rendering package.

このパッケージは、トーナメント表をWeb画面に表示するためのSVG生成処理を
まとめる場所。

views 層からは、基本的に tournament_svg_builder の公開関数を呼び出す。
ここではDB更新やフォーム処理は行わず、受け取ったトーナメントデータを
SVGで描画しやすい辞書・線・文字ブロックへ変換することだけを担当する。
"""

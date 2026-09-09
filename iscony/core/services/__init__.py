"""Business services for the core app.

このパッケージは、DBの状態を変える業務処理を置く場所。

operations.py:
  リーグ結果保存、トーナメント結果保存、順位再計算、後続Stage反映、進行表の差し替えなど、
  通常運用で発生する状態変更を扱う。

snapshots.py:
  操作前後の状態をJSON化して保存し、必要な範囲を復元する処理を扱う。

外部からは従来どおり core.services から主要な業務処理をimportできるよう、
operations.py の公開関数を再公開している。
新しいサービスを追加するときは、まず「通常運用の更新」なのか「スナップショット」なのかを
分けて考える。
"""

from .operations import *  # noqa: F401,F403
from .participants import *  # noqa: F401,F403
from .stage_setup import *  # noqa: F401,F403

def validate_game_score(
    pair1_games,
    pair2_games,
    winning_games
):

    # --------------------------------------
    # 数値保証
    # --------------------------------------
    if (
        not isinstance(pair1_games, int)
        or not isinstance(pair2_games, int)
    ):
        return "ゲーム数は数値で入力してください。"

    # --------------------------------------
    # マイナス禁止
    # --------------------------------------
    if pair1_games < 0 or pair2_games < 0:
        return "ゲーム数にマイナスは登録できません。"

    # --------------------------------------
    # 上限超過禁止
    # --------------------------------------
    if (
        pair1_games > winning_games
        or pair2_games > winning_games
    ):
        return "ゲーム数が上限を超えています。"

    # --------------------------------------
    # 同点禁止
    # --------------------------------------
    if pair1_games == pair2_games:
        return "同点は登録できません。"

    # --------------------------------------
    # 勝者ゲーム数チェック
    # --------------------------------------
    if (
        max(pair1_games, pair2_games)
        != winning_games
    ):
        return "勝者のゲーム数が正しくありません。"

    # --------------------------------------
    # 敗者側は winning_games 未満
    # --------------------------------------
    if (
        min(pair1_games, pair2_games)
        >= winning_games
    ):
        return "敗者のゲーム数が多すぎます。"

    return None



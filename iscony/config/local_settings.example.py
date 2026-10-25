# このファイルを local_settings.py という名前でコピーして値を書き換える。
# local_settings.py はGitに入れない。この見本ファイル自体は読み込まれない。

# 本番専用のランダムな秘密鍵に置き換える。開発環境と同じ値を使わない。
# 作成コマンド:
# python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
# SECRET_KEYを変えると、ログイン中の利用者はログアウトされる。
SECRET_KEY = "replace-with-a-production-only-random-secret-key"

# 本番では詳細なエラー情報を公開しない。
DEBUG = False

# 本番でアクセスするドメイン名に置き換える（スキームやパスは含めない）。
ALLOWED_HOSTS = ["example.com"]

# 本番用PostgreSQLの接続情報に置き換える。
# NAME: DB名、USER: 接続ユーザー、PASSWORD: パスワード、HOST: 接続先、PORT: ポート番号。
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "example_db",
        "USER": "example_user",
        "PASSWORD": "replace-with-database-password",
        "HOST": "127.0.0.1",
        "PORT": "5432",
    }
}

# collectstaticの出力先。Apacheの Alias /static/ の参照先と合わせる。
STATIC_ROOT = "/var/www/stproject/staticfiles"

# HTTPSの場合のみ、以下を有効にして本番のドメイン名に置き換える。
# CSRF_TRUSTED_ORIGINS = ["https://example.com"]
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True

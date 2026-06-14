"""収集層（Google Health バイタル・自己申告）。"""


class ConsentRevokedError(Exception):
    """ユーザーが健康データソースへのアクセス同意を取り消した（FR-012）。"""

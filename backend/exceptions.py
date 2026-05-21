class GradMatchError(Exception):
    """プロジェクト基底例外"""


class LLMResponseError(GradMatchError):
    """LLM API が不正なレスポンスを返した場合"""


class VerificationError(GradMatchError):
    """URL検証で回復不能なエラーが発生した場合"""


class DataLoadError(GradMatchError):
    """DB投入でエラーが発生した場合"""


class PhaseInputError(GradMatchError):
    """前Phaseの出力ファイルが不正または存在しない場合"""

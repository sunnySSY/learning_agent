"""阿里云百炼 text-embedding-v4 封装。

下面这些约束是 2026-09 对实际接口探测的结果，不是照抄文档：
- 单次请求最多 10 条文本，第 11 条直接 400（batch size is invalid），所以必须分批
- 合法维度只有 64/128/256/512/768/1024/1536/2048/3072
  官方文档写「最大 2048」，实际 API 还接受 3072，这里按 API 实测值校验
- 返回顺序与输入顺序一致（实测 batch=10 时 index 为 0..9），不需要按 index 重排
- text_type 在 OpenAI 兼容接口上被接受，用于 query/document 非对称检索

把这些规则封在这一层，上层不用关心。
"""

from langchain_core.embeddings import Embeddings
from openai import OpenAI

# 硬上限，超过直接 400，不要调大
MAX_BATCH = 10

# 实测合法值，取自接口报错信息
VALID_DIMENSIONS = (64, 128, 256, 512, 768, 1024, 1536, 2048, 3072)


class AliyunEmbeddings(Embeddings):
    """实现 LangChain 的 Embeddings 接口，可直接喂给 Chroma。

    use_text_type=True 时，文档用 text_type=document、查询用 text_type=query，
    这是 Qwen 系列 embedding 的非对称检索用法，一般能提升召回质量。
    若某天接口不再接受该参数，把它设成 False 即可退回对称模式。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
        batch_size: int = MAX_BATCH,
        use_text_type: bool = True,
    ):
        if dimensions not in VALID_DIMENSIONS:
            raise ValueError(
                f"维度 {dimensions} 不合法，只能是 {VALID_DIMENSIONS} 之一"
            )
        if not 1 <= batch_size <= MAX_BATCH:
            raise ValueError(f"batch_size 必须在 1..{MAX_BATCH} 之间，百炼单次最多 10 条")

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.use_text_type = use_text_type

    def _embed(self, texts: list[str], text_type: str) -> list[list[float]]:
        extra_body = {"text_type": text_type} if self.use_text_type else None
        vectors: list[list[float]] = []

        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self._client.embeddings.create(
                model=self.model,
                input=batch,
                dimensions=self.dimensions,
                encoding_format="float",
                extra_body=extra_body,
            )
            # 实测返回顺序与输入一致，这里仍按 index 排一次，避免接口行为变化时静默错位
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)

        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """索引文档时用。"""
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        """检索查询时用。"""
        return self._embed([text], "query")[0]

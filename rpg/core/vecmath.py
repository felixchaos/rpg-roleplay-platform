"""core.vecmath — 向量数学权威缝(单一真源)。

抽出前散落两处、数学等价的余弦相似度实现,统一到中立缝以便复用与寻根:

- ``cosine``:两个等长数值向量的余弦相似度。
  蓝本 = ``ingest.filters._cosine``(零向量判定写 ``if na == 0 or nb == 0: return 0.0``)。
  另一份 ``extract.resolve._cosine`` 写法是 ``... if na and nb else 0.0``——两者数学等价
  (``na == 0 or nb == 0`` 与 ``not (na and nb)`` 逻辑互补,零向量恒返回 0.0,非零向量走
  同一条 ``num / (na * nb)``),已自证后合一。

本模块只依赖标准库,不反向依赖任何业务模块——是中立缝而非柱子。行为逐字节保持,
两处调用方仍保留各自本地名 ``_cosine``(薄委托),外部签名不变。
"""
from __future__ import annotations

from collections.abc import Sequence


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度。任一向量为零向量时返回 0.0(而非除零)。"""
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return num / (na * nb)

"""Phase G W4 — extract.budget 预算估算器(确定性,无 DB 用 fake db)。"""
from extract.budget import MODEL_PRICING, cheapest_models, estimate


class _FakeDB:
    def __init__(self, n): self.n = n
    def execute(self, sql, params=None):
        self._n = self.n
        return self
    def fetchone(self):
        return {"c": self._n}


def test_estimate_scales_with_chapters_and_model():
    db = _FakeDB(866)
    flash = estimate(db, 1, model="gemini-3.5-flash")
    assert flash["ok"] and flash["chapters"] == 866
    assert 0.3 < flash["est_usd"] < 2.0  # 全书 flash 合理区间
    haiku = estimate(db, 1, model="claude-haiku-4-5")
    assert haiku["est_usd"] > flash["est_usd"]  # haiku 更贵
    assert haiku["model_tier"] == "haiku"
    batch = estimate(db, 1, model="gemini-3.5-flash", batch_discount=True)
    assert abs(batch["est_usd"] - flash["est_usd"] * 0.5) < 0.01  # 五折


def test_sample_chapters_caps():
    db = _FakeDB(866)
    s80 = estimate(db, 1, model="gemini-3.5-flash", sample_chapters=80)
    assert s80["chapters"] == 80 and s80["total_extractable"] == 866
    assert s80["est_usd"] < estimate(db, 1, model="gemini-3.5-flash")["est_usd"]


def test_zero_chapters():
    assert estimate(_FakeDB(0), 1)["ok"] is False


def test_cheapest_excludes_frontier():
    cm = cheapest_models()
    assert all(MODEL_PRICING[m]["tier"] != "frontier" for m in cm)


if __name__ == "__main__":
    test_estimate_scales_with_chapters_and_model(); test_sample_chapters_caps()
    test_zero_chapters(); test_cheapest_excludes_frontier(); print("OK")

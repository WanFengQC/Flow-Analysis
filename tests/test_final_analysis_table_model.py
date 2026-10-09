"""Data Tab 的 Final Analysis Table Model 展示边界测试。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from views.final_analysis_table_model import FinalAnalysisTableModel


class FinalAnalysisTableModelTest(unittest.TestCase):
    """确认搜索和排序只影响展示快照，不承担业务计算或改写最终顺序。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.final_rows = [
            {
                "month": "202608",
                "word": "weighted",
                "frequency": 2,
                "ratio": 0.25,
                "topPhrases": ["weighted stuffed animal"],
                "label": "3属性",
                "labelSource": "AI共识",
                "labelReason": "商品功能",
            },
            {
                "month": "202607",
                "word": "animal",
                "frequency": 5,
                "ratio": None,
                "topPhrases": [],
                "label": None,
                "labelSource": "AI失败",
                "labelReason": None,
            },
        ]
        self.model = FinalAnalysisTableModel(self.final_rows)

    def test_model_keeps_final_rows_and_formats_only_for_display(self):
        """Data Tab 的百分比/短语格式化不改变 Controller 传入的业务值。"""

        ratio_column = next(
            index
            for index, column in enumerate(self.model.columns)
            if column.field == "ratio"
        )
        phrase_column = next(
            index
            for index, column in enumerate(self.model.columns)
            if column.field == "topPhrases"
        )

        self.assertEqual(self.model.rowCount(), 2)
        self.assertEqual(self.model.data(self.model.index(0, ratio_column)), "25.00%")
        self.assertEqual(
            self.model.data(self.model.index(0, phrase_column)),
            "weighted stuffed animal",
        )
        self.assertEqual(self.final_rows[0]["ratio"], 0.25)
        self.assertEqual(self.final_rows[0]["topPhrases"], ["weighted stuffed animal"])

    def test_filter_and_sort_do_not_mutate_controller_final_order(self):
        """用户筛选/排序只改 Model 的展示索引，正式 final_rows 顺序保持不变。"""

        word_column = next(
            index
            for index, column in enumerate(self.model.columns)
            if column.field == "word"
        )
        self.model.set_filter_text("202607")
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.model.data(self.model.index(0, word_column)), "animal")

        self.model.set_filter_text("")
        self.model.sort(word_column, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.model.data(self.model.index(0, word_column)), "animal")
        self.assertEqual(
            [row["word"] for row in self.final_rows],
            ["weighted", "animal"],
        )


if __name__ == "__main__":
    unittest.main()

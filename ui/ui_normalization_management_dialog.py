# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'normalization_management_dialog.ui'
##
## Created by: Qt User Interface Compiler version 6.11.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QSpacerItem, QTableView,
    QVBoxLayout, QWidget)

class Ui_NormalizationManagementDialog(object):
    def setupUi(self, NormalizationManagementDialog):
        if not NormalizationManagementDialog.objectName():
            NormalizationManagementDialog.setObjectName(u"NormalizationManagementDialog")
        NormalizationManagementDialog.setModal(False)
        self.normalizationManagementRootLayout = QVBoxLayout(NormalizationManagementDialog)
        self.normalizationManagementRootLayout.setObjectName(u"normalizationManagementRootLayout")
        self.normalizationManagementRootLayout.setContentsMargins(14, 14, 14, 14)
        self.normalizationManagementFilterLayout = QHBoxLayout()
        self.normalizationManagementFilterLayout.setObjectName(u"normalizationManagementFilterLayout")
        self.searchLineEdit = QLineEdit(NormalizationManagementDialog)
        self.searchLineEdit.setObjectName(u"searchLineEdit")
        self.searchLineEdit.setMinimumSize(QSize(280, 32))
        self.searchLineEdit.setClearButtonEnabled(True)

        self.normalizationManagementFilterLayout.addWidget(self.searchLineEdit)

        self.ruleTypeComboBox = QComboBox(NormalizationManagementDialog)
        self.ruleTypeComboBox.setObjectName(u"ruleTypeComboBox")
        self.ruleTypeComboBox.setMinimumSize(QSize(130, 32))

        self.normalizationManagementFilterLayout.addWidget(self.ruleTypeComboBox)

        self.refreshButton = QPushButton(NormalizationManagementDialog)
        self.refreshButton.setObjectName(u"refreshButton")

        self.normalizationManagementFilterLayout.addWidget(self.refreshButton)

        self.normalizationManagementFilterSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.normalizationManagementFilterLayout.addItem(self.normalizationManagementFilterSpacer)

        self.countLabel = QLabel(NormalizationManagementDialog)
        self.countLabel.setObjectName(u"countLabel")

        self.normalizationManagementFilterLayout.addWidget(self.countLabel)


        self.normalizationManagementRootLayout.addLayout(self.normalizationManagementFilterLayout)

        self.rulesTableView = QTableView(NormalizationManagementDialog)
        self.rulesTableView.setObjectName(u"rulesTableView")
        self.rulesTableView.setAlternatingRowColors(True)
        self.rulesTableView.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rulesTableView.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rulesTableView.setSortingEnabled(False)

        self.normalizationManagementRootLayout.addWidget(self.rulesTableView)

        self.normalizationManagementFooterLayout = QHBoxLayout()
        self.normalizationManagementFooterLayout.setObjectName(u"normalizationManagementFooterLayout")
        self.addButton = QPushButton(NormalizationManagementDialog)
        self.addButton.setObjectName(u"addButton")

        self.normalizationManagementFooterLayout.addWidget(self.addButton)

        self.editButton = QPushButton(NormalizationManagementDialog)
        self.editButton.setObjectName(u"editButton")

        self.normalizationManagementFooterLayout.addWidget(self.editButton)

        self.revokeButton = QPushButton(NormalizationManagementDialog)
        self.revokeButton.setObjectName(u"revokeButton")

        self.normalizationManagementFooterLayout.addWidget(self.revokeButton)

        self.historyButton = QPushButton(NormalizationManagementDialog)
        self.historyButton.setObjectName(u"historyButton")

        self.normalizationManagementFooterLayout.addWidget(self.historyButton)

        self.normalizationManagementFooterSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.normalizationManagementFooterLayout.addItem(self.normalizationManagementFooterSpacer)

        self.previousPageButton = QPushButton(NormalizationManagementDialog)
        self.previousPageButton.setObjectName(u"previousPageButton")

        self.normalizationManagementFooterLayout.addWidget(self.previousPageButton)

        self.pageLabel = QLabel(NormalizationManagementDialog)
        self.pageLabel.setObjectName(u"pageLabel")

        self.normalizationManagementFooterLayout.addWidget(self.pageLabel)

        self.nextPageButton = QPushButton(NormalizationManagementDialog)
        self.nextPageButton.setObjectName(u"nextPageButton")

        self.normalizationManagementFooterLayout.addWidget(self.nextPageButton)


        self.normalizationManagementRootLayout.addLayout(self.normalizationManagementFooterLayout)


        self.retranslateUi(NormalizationManagementDialog)

        QMetaObject.connectSlotsByName(NormalizationManagementDialog)
    # setupUi

    def retranslateUi(self, NormalizationManagementDialog):
        NormalizationManagementDialog.setWindowTitle(QCoreApplication.translate("NormalizationManagementDialog", u"\u5f52\u4e00\u5316\u7ba1\u7406", None))
        self.searchLineEdit.setPlaceholderText(QCoreApplication.translate("NormalizationManagementDialog", u"\u641c\u7d22 variant \u6216 canonical", None))
        self.refreshButton.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u5237\u65b0", None))
        self.countLabel.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u5f53\u524d\u6709\u6548\u89c4\u5219\uff1a0", None))
        self.addButton.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u65b0\u589e\u89c4\u5219", None))
        self.editButton.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u7f16\u8f91", None))
        self.revokeButton.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u64a4\u9500", None))
        self.historyButton.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u67e5\u770b\u5386\u53f2", None))
        self.previousPageButton.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u4e0a\u4e00\u9875", None))
        self.pageLabel.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u7b2c 1 \u9875", None))
        self.nextPageButton.setText(QCoreApplication.translate("NormalizationManagementDialog", u"\u4e0b\u4e00\u9875", None))
    # retranslateUi


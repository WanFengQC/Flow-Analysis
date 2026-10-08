# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'tagging_label_management_dialog.ui'
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

class Ui_TaggingLabelManagementDialog(object):
    def setupUi(self, TaggingLabelManagementDialog):
        if not TaggingLabelManagementDialog.objectName():
            TaggingLabelManagementDialog.setObjectName(u"TaggingLabelManagementDialog")
        TaggingLabelManagementDialog.setModal(False)
        self.taggingManagementRootLayout = QVBoxLayout(TaggingLabelManagementDialog)
        self.taggingManagementRootLayout.setObjectName(u"taggingManagementRootLayout")
        self.taggingManagementRootLayout.setContentsMargins(14, 14, 14, 14)
        self.taggingManagementFilterLayout = QHBoxLayout()
        self.taggingManagementFilterLayout.setObjectName(u"taggingManagementFilterLayout")
        self.searchLineEdit = QLineEdit(TaggingLabelManagementDialog)
        self.searchLineEdit.setObjectName(u"searchLineEdit")
        self.searchLineEdit.setMinimumSize(QSize(220, 32))
        self.searchLineEdit.setClearButtonEnabled(True)

        self.taggingManagementFilterLayout.addWidget(self.searchLineEdit)

        self.categoryComboBox = QComboBox(TaggingLabelManagementDialog)
        self.categoryComboBox.setObjectName(u"categoryComboBox")
        self.categoryComboBox.setMinimumSize(QSize(130, 32))

        self.taggingManagementFilterLayout.addWidget(self.categoryComboBox)

        self.labelComboBox = QComboBox(TaggingLabelManagementDialog)
        self.labelComboBox.setObjectName(u"labelComboBox")
        self.labelComboBox.setMinimumSize(QSize(130, 32))

        self.taggingManagementFilterLayout.addWidget(self.labelComboBox)

        self.sourceComboBox = QComboBox(TaggingLabelManagementDialog)
        self.sourceComboBox.setObjectName(u"sourceComboBox")
        self.sourceComboBox.setMinimumSize(QSize(150, 32))

        self.taggingManagementFilterLayout.addWidget(self.sourceComboBox)

        self.refreshButton = QPushButton(TaggingLabelManagementDialog)
        self.refreshButton.setObjectName(u"refreshButton")

        self.taggingManagementFilterLayout.addWidget(self.refreshButton)

        self.taggingManagementFilterSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.taggingManagementFilterLayout.addItem(self.taggingManagementFilterSpacer)

        self.countLabel = QLabel(TaggingLabelManagementDialog)
        self.countLabel.setObjectName(u"countLabel")

        self.taggingManagementFilterLayout.addWidget(self.countLabel)


        self.taggingManagementRootLayout.addLayout(self.taggingManagementFilterLayout)

        self.labelsTableView = QTableView(TaggingLabelManagementDialog)
        self.labelsTableView.setObjectName(u"labelsTableView")
        self.labelsTableView.setAlternatingRowColors(True)
        self.labelsTableView.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.labelsTableView.setSelectionMode(QAbstractItemView.SingleSelection)
        self.labelsTableView.setSortingEnabled(False)

        self.taggingManagementRootLayout.addWidget(self.labelsTableView)

        self.taggingManagementFooterLayout = QHBoxLayout()
        self.taggingManagementFooterLayout.setObjectName(u"taggingManagementFooterLayout")
        self.addButton = QPushButton(TaggingLabelManagementDialog)
        self.addButton.setObjectName(u"addButton")

        self.taggingManagementFooterLayout.addWidget(self.addButton)

        self.editButton = QPushButton(TaggingLabelManagementDialog)
        self.editButton.setObjectName(u"editButton")

        self.taggingManagementFooterLayout.addWidget(self.editButton)

        self.deleteButton = QPushButton(TaggingLabelManagementDialog)
        self.deleteButton.setObjectName(u"deleteButton")

        self.taggingManagementFooterLayout.addWidget(self.deleteButton)

        self.historyButton = QPushButton(TaggingLabelManagementDialog)
        self.historyButton.setObjectName(u"historyButton")

        self.taggingManagementFooterLayout.addWidget(self.historyButton)

        self.taggingManagementFooterSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.taggingManagementFooterLayout.addItem(self.taggingManagementFooterSpacer)

        self.previousPageButton = QPushButton(TaggingLabelManagementDialog)
        self.previousPageButton.setObjectName(u"previousPageButton")

        self.taggingManagementFooterLayout.addWidget(self.previousPageButton)

        self.pageLabel = QLabel(TaggingLabelManagementDialog)
        self.pageLabel.setObjectName(u"pageLabel")

        self.taggingManagementFooterLayout.addWidget(self.pageLabel)

        self.nextPageButton = QPushButton(TaggingLabelManagementDialog)
        self.nextPageButton.setObjectName(u"nextPageButton")

        self.taggingManagementFooterLayout.addWidget(self.nextPageButton)


        self.taggingManagementRootLayout.addLayout(self.taggingManagementFooterLayout)


        self.retranslateUi(TaggingLabelManagementDialog)

        QMetaObject.connectSlotsByName(TaggingLabelManagementDialog)
    # setupUi

    def retranslateUi(self, TaggingLabelManagementDialog):
        TaggingLabelManagementDialog.setWindowTitle(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u6807\u7b7e\u7ba1\u7406", None))
        self.searchLineEdit.setPlaceholderText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u641c\u7d22\u6807\u51c6\u8bcd", None))
        self.refreshButton.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u5237\u65b0", None))
        self.countLabel.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u5f53\u524d\u6709\u6548\u6807\u7b7e\uff1a0", None))
        self.addButton.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u65b0\u589e\u6807\u7b7e", None))
        self.editButton.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u7f16\u8f91", None))
        self.deleteButton.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u5220\u9664", None))
        self.historyButton.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u67e5\u770b\u5386\u53f2", None))
        self.previousPageButton.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u4e0a\u4e00\u9875", None))
        self.pageLabel.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u7b2c 1 \u9875", None))
        self.nextPageButton.setText(QCoreApplication.translate("TaggingLabelManagementDialog", u"\u4e0b\u4e00\u9875", None))
    # retranslateUi


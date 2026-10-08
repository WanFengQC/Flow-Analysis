# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'tagging_review_dialog.ui'
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
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpacerItem,
    QSplitter, QVBoxLayout, QWidget)

class Ui_TaggingReviewDialog(object):
    def setupUi(self, TaggingReviewDialog):
        if not TaggingReviewDialog.objectName():
            TaggingReviewDialog.setObjectName(u"TaggingReviewDialog")
        TaggingReviewDialog.setModal(False)
        self.taggingReviewRootLayout = QVBoxLayout(TaggingReviewDialog)
        self.taggingReviewRootLayout.setSpacing(10)
        self.taggingReviewRootLayout.setObjectName(u"taggingReviewRootLayout")
        self.taggingReviewRootLayout.setContentsMargins(14, 14, 14, 14)
        self.taggingReviewHeaderLayout = QHBoxLayout()
        self.taggingReviewHeaderLayout.setObjectName(u"taggingReviewHeaderLayout")
        self.taggingReviewTitleLabel = QLabel(TaggingReviewDialog)
        self.taggingReviewTitleLabel.setObjectName(u"taggingReviewTitleLabel")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        self.taggingReviewTitleLabel.setFont(font)

        self.taggingReviewHeaderLayout.addWidget(self.taggingReviewTitleLabel)

        self.taggingReviewHeaderSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.taggingReviewHeaderLayout.addItem(self.taggingReviewHeaderSpacer)

        self.taggingReviewProgressLabel = QLabel(TaggingReviewDialog)
        self.taggingReviewProgressLabel.setObjectName(u"taggingReviewProgressLabel")

        self.taggingReviewHeaderLayout.addWidget(self.taggingReviewProgressLabel)


        self.taggingReviewRootLayout.addLayout(self.taggingReviewHeaderLayout)

        self.taggingReviewSearchLineEdit = QLineEdit(TaggingReviewDialog)
        self.taggingReviewSearchLineEdit.setObjectName(u"taggingReviewSearchLineEdit")
        self.taggingReviewSearchLineEdit.setMinimumSize(QSize(0, 32))
        self.taggingReviewSearchLineEdit.setClearButtonEnabled(True)

        self.taggingReviewRootLayout.addWidget(self.taggingReviewSearchLineEdit)

        self.taggingReviewSplitter = QSplitter(TaggingReviewDialog)
        self.taggingReviewSplitter.setObjectName(u"taggingReviewSplitter")
        self.taggingReviewSplitter.setOrientation(Qt.Horizontal)
        self.taggingReviewSplitter.setChildrenCollapsible(False)
        self.taggingReviewListPanel = QWidget(self.taggingReviewSplitter)
        self.taggingReviewListPanel.setObjectName(u"taggingReviewListPanel")
        self.taggingReviewListLayout = QVBoxLayout(self.taggingReviewListPanel)
        self.taggingReviewListLayout.setObjectName(u"taggingReviewListLayout")
        self.taggingReviewListLayout.setContentsMargins(0, 0, 0, 0)
        self.taggingReviewListTitleLabel = QLabel(self.taggingReviewListPanel)
        self.taggingReviewListTitleLabel.setObjectName(u"taggingReviewListTitleLabel")

        self.taggingReviewListLayout.addWidget(self.taggingReviewListTitleLabel)

        self.taggingReviewListWidget = QListWidget(self.taggingReviewListPanel)
        self.taggingReviewListWidget.setObjectName(u"taggingReviewListWidget")
        self.taggingReviewListWidget.setMinimumSize(QSize(260, 0))
        self.taggingReviewListWidget.setAlternatingRowColors(True)

        self.taggingReviewListLayout.addWidget(self.taggingReviewListWidget)

        self.taggingReviewEmptyLabel = QLabel(self.taggingReviewListPanel)
        self.taggingReviewEmptyLabel.setObjectName(u"taggingReviewEmptyLabel")
        self.taggingReviewEmptyLabel.setWordWrap(True)

        self.taggingReviewListLayout.addWidget(self.taggingReviewEmptyLabel)

        self.taggingReviewSplitter.addWidget(self.taggingReviewListPanel)
        self.taggingReviewDetailScrollArea = QScrollArea(self.taggingReviewSplitter)
        self.taggingReviewDetailScrollArea.setObjectName(u"taggingReviewDetailScrollArea")
        self.taggingReviewDetailScrollArea.setWidgetResizable(True)
        self.taggingReviewDetailContent = QWidget()
        self.taggingReviewDetailContent.setObjectName(u"taggingReviewDetailContent")
        self.taggingReviewDetailLayout = QVBoxLayout(self.taggingReviewDetailContent)
        self.taggingReviewDetailLayout.setObjectName(u"taggingReviewDetailLayout")
        self.taggingReviewBasicFormLayout = QFormLayout()
        self.taggingReviewBasicFormLayout.setObjectName(u"taggingReviewBasicFormLayout")
        self.taggingWordLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingWordLabel.setObjectName(u"taggingWordLabel")

        self.taggingReviewBasicFormLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.taggingWordLabel)

        self.taggingWordValueLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingWordValueLabel.setObjectName(u"taggingWordValueLabel")
        self.taggingWordValueLabel.setWordWrap(True)

        self.taggingReviewBasicFormLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.taggingWordValueLabel)

        self.taggingMonthLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingMonthLabel.setObjectName(u"taggingMonthLabel")

        self.taggingReviewBasicFormLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.taggingMonthLabel)

        self.taggingMonthValueLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingMonthValueLabel.setObjectName(u"taggingMonthValueLabel")

        self.taggingReviewBasicFormLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.taggingMonthValueLabel)

        self.taggingCategoryLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingCategoryLabel.setObjectName(u"taggingCategoryLabel")

        self.taggingReviewBasicFormLayout.setWidget(2, QFormLayout.ItemRole.LabelRole, self.taggingCategoryLabel)

        self.taggingCategoryValueLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingCategoryValueLabel.setObjectName(u"taggingCategoryValueLabel")

        self.taggingReviewBasicFormLayout.setWidget(2, QFormLayout.ItemRole.FieldRole, self.taggingCategoryValueLabel)

        self.taggingTopPhrasesLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingTopPhrasesLabel.setObjectName(u"taggingTopPhrasesLabel")

        self.taggingReviewBasicFormLayout.setWidget(3, QFormLayout.ItemRole.LabelRole, self.taggingTopPhrasesLabel)

        self.taggingTopPhrasesTextEdit = QPlainTextEdit(self.taggingReviewDetailContent)
        self.taggingTopPhrasesTextEdit.setObjectName(u"taggingTopPhrasesTextEdit")
        self.taggingTopPhrasesTextEdit.setReadOnly(True)
        self.taggingTopPhrasesTextEdit.setMaximumHeight(80)

        self.taggingReviewBasicFormLayout.setWidget(3, QFormLayout.ItemRole.FieldRole, self.taggingTopPhrasesTextEdit)

        self.taggingRepresentativeAsinLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingRepresentativeAsinLabel.setObjectName(u"taggingRepresentativeAsinLabel")

        self.taggingReviewBasicFormLayout.setWidget(4, QFormLayout.ItemRole.LabelRole, self.taggingRepresentativeAsinLabel)

        self.taggingRepresentativeAsinValueLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingRepresentativeAsinValueLabel.setObjectName(u"taggingRepresentativeAsinValueLabel")

        self.taggingReviewBasicFormLayout.setWidget(4, QFormLayout.ItemRole.FieldRole, self.taggingRepresentativeAsinValueLabel)

        self.taggingContextSourceLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingContextSourceLabel.setObjectName(u"taggingContextSourceLabel")

        self.taggingReviewBasicFormLayout.setWidget(5, QFormLayout.ItemRole.LabelRole, self.taggingContextSourceLabel)

        self.taggingContextSourceValueLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingContextSourceValueLabel.setObjectName(u"taggingContextSourceValueLabel")

        self.taggingReviewBasicFormLayout.setWidget(5, QFormLayout.ItemRole.FieldRole, self.taggingContextSourceValueLabel)


        self.taggingReviewDetailLayout.addLayout(self.taggingReviewBasicFormLayout)

        self.taggingProductContextLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingProductContextLabel.setObjectName(u"taggingProductContextLabel")

        self.taggingReviewDetailLayout.addWidget(self.taggingProductContextLabel)

        self.taggingProductContextTextEdit = QPlainTextEdit(self.taggingReviewDetailContent)
        self.taggingProductContextTextEdit.setObjectName(u"taggingProductContextTextEdit")
        self.taggingProductContextTextEdit.setReadOnly(True)
        self.taggingProductContextTextEdit.setMaximumHeight(140)

        self.taggingReviewDetailLayout.addWidget(self.taggingProductContextTextEdit)

        self.taggingProviderOpinionLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingProviderOpinionLabel.setObjectName(u"taggingProviderOpinionLabel")

        self.taggingReviewDetailLayout.addWidget(self.taggingProviderOpinionLabel)

        self.taggingProviderPanelsLayout = QHBoxLayout()
        self.taggingProviderPanelsLayout.setObjectName(u"taggingProviderPanelsLayout")
        self.openaiProviderGroup = QGroupBox(self.taggingReviewDetailContent)
        self.openaiProviderGroup.setObjectName(u"openaiProviderGroup")
        self.openaiProviderLayout = QVBoxLayout(self.openaiProviderGroup)
        self.openaiProviderLayout.setObjectName(u"openaiProviderLayout")
        self.openaiProviderLabelValue = QLabel(self.openaiProviderGroup)
        self.openaiProviderLabelValue.setObjectName(u"openaiProviderLabelValue")
        self.openaiProviderLabelValue.setWordWrap(True)

        self.openaiProviderLayout.addWidget(self.openaiProviderLabelValue)

        self.openaiProviderReasonTextEdit = QPlainTextEdit(self.openaiProviderGroup)
        self.openaiProviderReasonTextEdit.setObjectName(u"openaiProviderReasonTextEdit")
        self.openaiProviderReasonTextEdit.setReadOnly(True)
        self.openaiProviderReasonTextEdit.setMaximumHeight(100)

        self.openaiProviderLayout.addWidget(self.openaiProviderReasonTextEdit)

        self.adoptOpenaiButton = QPushButton(self.openaiProviderGroup)
        self.adoptOpenaiButton.setObjectName(u"adoptOpenaiButton")

        self.openaiProviderLayout.addWidget(self.adoptOpenaiButton)


        self.taggingProviderPanelsLayout.addWidget(self.openaiProviderGroup)

        self.anthropicProviderGroup = QGroupBox(self.taggingReviewDetailContent)
        self.anthropicProviderGroup.setObjectName(u"anthropicProviderGroup")
        self.anthropicProviderLayout = QVBoxLayout(self.anthropicProviderGroup)
        self.anthropicProviderLayout.setObjectName(u"anthropicProviderLayout")
        self.anthropicProviderLabelValue = QLabel(self.anthropicProviderGroup)
        self.anthropicProviderLabelValue.setObjectName(u"anthropicProviderLabelValue")
        self.anthropicProviderLabelValue.setWordWrap(True)

        self.anthropicProviderLayout.addWidget(self.anthropicProviderLabelValue)

        self.anthropicProviderReasonTextEdit = QPlainTextEdit(self.anthropicProviderGroup)
        self.anthropicProviderReasonTextEdit.setObjectName(u"anthropicProviderReasonTextEdit")
        self.anthropicProviderReasonTextEdit.setReadOnly(True)
        self.anthropicProviderReasonTextEdit.setMaximumHeight(100)

        self.anthropicProviderLayout.addWidget(self.anthropicProviderReasonTextEdit)

        self.adoptAnthropicButton = QPushButton(self.anthropicProviderGroup)
        self.adoptAnthropicButton.setObjectName(u"adoptAnthropicButton")

        self.anthropicProviderLayout.addWidget(self.adoptAnthropicButton)


        self.taggingProviderPanelsLayout.addWidget(self.anthropicProviderGroup)

        self.googleProviderGroup = QGroupBox(self.taggingReviewDetailContent)
        self.googleProviderGroup.setObjectName(u"googleProviderGroup")
        self.googleProviderLayout = QVBoxLayout(self.googleProviderGroup)
        self.googleProviderLayout.setObjectName(u"googleProviderLayout")
        self.googleProviderLabelValue = QLabel(self.googleProviderGroup)
        self.googleProviderLabelValue.setObjectName(u"googleProviderLabelValue")
        self.googleProviderLabelValue.setWordWrap(True)

        self.googleProviderLayout.addWidget(self.googleProviderLabelValue)

        self.googleProviderReasonTextEdit = QPlainTextEdit(self.googleProviderGroup)
        self.googleProviderReasonTextEdit.setObjectName(u"googleProviderReasonTextEdit")
        self.googleProviderReasonTextEdit.setReadOnly(True)
        self.googleProviderReasonTextEdit.setMaximumHeight(100)

        self.googleProviderLayout.addWidget(self.googleProviderReasonTextEdit)

        self.adoptGoogleButton = QPushButton(self.googleProviderGroup)
        self.adoptGoogleButton.setObjectName(u"adoptGoogleButton")

        self.googleProviderLayout.addWidget(self.adoptGoogleButton)


        self.taggingProviderPanelsLayout.addWidget(self.googleProviderGroup)


        self.taggingReviewDetailLayout.addLayout(self.taggingProviderPanelsLayout)

        self.taggingOpinionSummaryLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingOpinionSummaryLabel.setObjectName(u"taggingOpinionSummaryLabel")
        self.taggingOpinionSummaryLabel.setWordWrap(True)

        self.taggingReviewDetailLayout.addWidget(self.taggingOpinionSummaryLabel)

        self.taggingReviewSeparator = QFrame(self.taggingReviewDetailContent)
        self.taggingReviewSeparator.setObjectName(u"taggingReviewSeparator")
        self.taggingReviewSeparator.setFrameShape(QFrame.HLine)
        self.taggingReviewSeparator.setFrameShadow(QFrame.Sunken)

        self.taggingReviewDetailLayout.addWidget(self.taggingReviewSeparator)

        self.taggingHumanDecisionTitleLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingHumanDecisionTitleLabel.setObjectName(u"taggingHumanDecisionTitleLabel")
        font1 = QFont()
        font1.setBold(True)
        self.taggingHumanDecisionTitleLabel.setFont(font1)

        self.taggingReviewDetailLayout.addWidget(self.taggingHumanDecisionTitleLabel)

        self.taggingHumanDecisionFormLayout = QFormLayout()
        self.taggingHumanDecisionFormLayout.setObjectName(u"taggingHumanDecisionFormLayout")
        self.taggingHumanLabelLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingHumanLabelLabel.setObjectName(u"taggingHumanLabelLabel")

        self.taggingHumanDecisionFormLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.taggingHumanLabelLabel)

        self.taggingHumanLabelComboBox = QComboBox(self.taggingReviewDetailContent)
        self.taggingHumanLabelComboBox.setObjectName(u"taggingHumanLabelComboBox")
        self.taggingHumanLabelComboBox.setMinimumSize(QSize(0, 32))

        self.taggingHumanDecisionFormLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.taggingHumanLabelComboBox)

        self.taggingHumanReasonLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingHumanReasonLabel.setObjectName(u"taggingHumanReasonLabel")

        self.taggingHumanDecisionFormLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.taggingHumanReasonLabel)

        self.taggingHumanReasonTextEdit = QPlainTextEdit(self.taggingReviewDetailContent)
        self.taggingHumanReasonTextEdit.setObjectName(u"taggingHumanReasonTextEdit")
        self.taggingHumanReasonTextEdit.setMinimumSize(QSize(0, 80))

        self.taggingHumanDecisionFormLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.taggingHumanReasonTextEdit)


        self.taggingReviewDetailLayout.addLayout(self.taggingHumanDecisionFormLayout)

        self.confirmTaggingReviewButton = QPushButton(self.taggingReviewDetailContent)
        self.confirmTaggingReviewButton.setObjectName(u"confirmTaggingReviewButton")
        self.confirmTaggingReviewButton.setMinimumSize(QSize(0, 34))

        self.taggingReviewDetailLayout.addWidget(self.confirmTaggingReviewButton)

        self.taggingReviewSaveStatusLabel = QLabel(self.taggingReviewDetailContent)
        self.taggingReviewSaveStatusLabel.setObjectName(u"taggingReviewSaveStatusLabel")
        self.taggingReviewSaveStatusLabel.setWordWrap(True)

        self.taggingReviewDetailLayout.addWidget(self.taggingReviewSaveStatusLabel)

        self.taggingReviewBottomSpacer = QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.taggingReviewDetailLayout.addItem(self.taggingReviewBottomSpacer)

        self.taggingReviewDetailScrollArea.setWidget(self.taggingReviewDetailContent)
        self.taggingReviewSplitter.addWidget(self.taggingReviewDetailScrollArea)

        self.taggingReviewRootLayout.addWidget(self.taggingReviewSplitter)


        self.retranslateUi(TaggingReviewDialog)

        QMetaObject.connectSlotsByName(TaggingReviewDialog)
    # setupUi

    def retranslateUi(self, TaggingReviewDialog):
        TaggingReviewDialog.setWindowTitle(QCoreApplication.translate("TaggingReviewDialog", u"AI \u6807\u7b7e\u4eba\u5de5\u5ba1\u6838", None))
        self.taggingReviewTitleLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"AI \u6807\u7b7e\u4eba\u5de5\u5ba1\u6838", None))
        self.taggingReviewProgressLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u5df2\u5b8c\u6210 0 / 0\uff0c\u5f85\u5ba1\u6838 0", None))
        self.taggingReviewSearchLineEdit.setPlaceholderText(QCoreApplication.translate("TaggingReviewDialog", u"\u641c\u7d22 word / top phrase", None))
        self.taggingReviewListTitleLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u5f85\u5ba1\u6838\u8bcd", None))
        self.taggingReviewEmptyLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u672c\u8f6e AI \u5206\u6b67\u5df2\u5168\u90e8\u4eba\u5de5\u786e\u8ba4\u3002", None))
        self.taggingWordLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"Word", None))
        self.taggingWordValueLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.taggingMonthLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u6708\u4efd", None))
        self.taggingMonthValueLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.taggingCategoryLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u54c1\u7c7b", None))
        self.taggingCategoryValueLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.taggingTopPhrasesLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"Top Phrases", None))
        self.taggingRepresentativeAsinLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u4ee3\u8868 ASIN", None))
        self.taggingRepresentativeAsinValueLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.taggingContextSourceLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u80cc\u666f\u6765\u6e90", None))
        self.taggingContextSourceValueLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.taggingProductContextLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u4ea7\u54c1\u80cc\u666f\u6458\u8981", None))
        self.taggingProviderOpinionLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u6a21\u578b\u610f\u89c1", None))
        self.openaiProviderGroup.setTitle(QCoreApplication.translate("TaggingReviewDialog", u"GPT-6 Sol", None))
        self.openaiProviderLabelValue.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.adoptOpenaiButton.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u91c7\u7528\u6b64\u7ed3\u679c", None))
        self.anthropicProviderGroup.setTitle(QCoreApplication.translate("TaggingReviewDialog", u"Claude Sonnet 5", None))
        self.anthropicProviderLabelValue.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.adoptAnthropicButton.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u91c7\u7528\u6b64\u7ed3\u679c", None))
        self.googleProviderGroup.setTitle(QCoreApplication.translate("TaggingReviewDialog", u"Gemini 3.8 Flash", None))
        self.googleProviderLabelValue.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.adoptGoogleButton.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u91c7\u7528\u6b64\u7ed3\u679c", None))
        self.taggingOpinionSummaryLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u2014", None))
        self.taggingHumanDecisionTitleLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u4eba\u5de5\u6700\u7ec8\u6807\u7b7e", None))
        self.taggingHumanLabelLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u4eba\u5de5\u6807\u7b7e", None))
        self.taggingHumanReasonLabel.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u4eba\u5de5\u539f\u56e0", None))
        self.confirmTaggingReviewButton.setText(QCoreApplication.translate("TaggingReviewDialog", u"\u786e\u8ba4\u4eba\u5de5\u6807\u7b7e", None))
        self.taggingReviewSaveStatusLabel.setText("")
    # retranslateUi

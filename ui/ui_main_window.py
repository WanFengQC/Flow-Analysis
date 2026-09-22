# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_window.ui'
##
## Created by: Qt User Interface Compiler version 6.11.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient,
    QCursor, QFont, QFontDatabase, QGradient,
    QIcon, QImage, QKeySequence, QLinearGradient,
    QPainter, QPalette, QPixmap, QRadialGradient,
    QTransform)
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMenu, QMenuBar, QPushButton,
    QScrollArea, QSizePolicy, QSpacerItem, QSplitter,
    QStackedWidget, QStatusBar, QTabWidget, QTableView,
    QToolButton, QVBoxLayout, QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1200, 800)
        self.actionAI = QAction(MainWindow)
        self.actionAI.setObjectName(u"actionAI")
        self.action = QAction(MainWindow)
        self.action.setObjectName(u"action")
        self.action_2 = QAction(MainWindow)
        self.action_2.setObjectName(u"action_2")
        self.action_3 = QAction(MainWindow)
        self.action_3.setObjectName(u"action_3")
        self.action_4 = QAction(MainWindow)
        self.action_4.setObjectName(u"action_4")
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.centralLayout = QVBoxLayout(self.centralwidget)
        self.centralLayout.setObjectName(u"centralLayout")
        self.centralLayout.setContentsMargins(0, 0, 0, 0)
        self.mainSplitter = QSplitter(self.centralwidget)
        self.mainSplitter.setObjectName(u"mainSplitter")
        self.mainSplitter.setOrientation(Qt.Orientation.Horizontal)
        self.mainSplitter.setOpaqueResize(True)
        self.mainSplitter.setChildrenCollapsible(False)
        self.parameterPanel = QWidget(self.mainSplitter)
        self.parameterPanel.setObjectName(u"parameterPanel")
        self.parameterPanel.setMinimumSize(QSize(260, 0))
        self.parameterLayout = QVBoxLayout(self.parameterPanel)
        self.parameterLayout.setSpacing(10)
        self.parameterLayout.setObjectName(u"parameterLayout")
        self.parameterLayout.setContentsMargins(12, 12, 12, 12)
        self.parameterTitleLabel = QLabel(self.parameterPanel)
        self.parameterTitleLabel.setObjectName(u"parameterTitleLabel")
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        self.parameterTitleLabel.setFont(font)

        self.parameterLayout.addWidget(self.parameterTitleLabel)

        self.asinLabel = QLabel(self.parameterPanel)
        self.asinLabel.setObjectName(u"asinLabel")

        self.parameterLayout.addWidget(self.asinLabel)

        self.asinInputLayout = QHBoxLayout()
        self.asinInputLayout.setSpacing(6)
        self.asinInputLayout.setObjectName(u"asinInputLayout")
        self.asinInputLayout.setContentsMargins(0, 0, 0, 0)
        self.asinLineEdit = QLineEdit(self.parameterPanel)
        self.asinLineEdit.setObjectName(u"asinLineEdit")
        self.asinLineEdit.setMinimumSize(QSize(0, 32))
        self.asinLineEdit.setClearButtonEnabled(True)

        self.asinInputLayout.addWidget(self.asinLineEdit)

        self.asinQueryButton = QToolButton(self.parameterPanel)
        self.asinQueryButton.setObjectName(u"asinQueryButton")
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.asinQueryButton.sizePolicy().hasHeightForWidth())
        self.asinQueryButton.setSizePolicy(sizePolicy)
        self.asinQueryButton.setMinimumSize(QSize(0, 32))

        self.asinInputLayout.addWidget(self.asinQueryButton)


        self.parameterLayout.addLayout(self.asinInputLayout)

        self.dateRangeLabel = QLabel(self.parameterPanel)
        self.dateRangeLabel.setObjectName(u"dateRangeLabel")

        self.parameterLayout.addWidget(self.dateRangeLabel)

        self.monthSelectorContainerLayout = QVBoxLayout()
        self.monthSelectorContainerLayout.setObjectName(u"monthSelectorContainerLayout")
        self.monthSelectorContainerLayout.setContentsMargins(0, 0, 0, 0)

        self.parameterLayout.addLayout(self.monthSelectorContainerLayout)

        self.analysisTypeLabel = QLabel(self.parameterPanel)
        self.analysisTypeLabel.setObjectName(u"analysisTypeLabel")

        self.parameterLayout.addWidget(self.analysisTypeLabel)

        self.analysisTypeComboBox = QComboBox(self.parameterPanel)
        self.analysisTypeComboBox.addItem("")
        self.analysisTypeComboBox.addItem("")
        self.analysisTypeComboBox.setObjectName(u"analysisTypeComboBox")
        self.analysisTypeComboBox.setMinimumSize(QSize(0, 32))

        self.parameterLayout.addWidget(self.analysisTypeComboBox)

        self.parameterVerticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.parameterLayout.addItem(self.parameterVerticalSpacer)

        self.analysisActionLayout = QHBoxLayout()
        self.analysisActionLayout.setObjectName(u"analysisActionLayout")
        self.analysisActionLayout.setContentsMargins(0, 0, 0, 0)
        self.startAnalysisButton = QPushButton(self.parameterPanel)
        self.startAnalysisButton.setObjectName(u"startAnalysisButton")

        self.analysisActionLayout.addWidget(self.startAnalysisButton)

        self.cancelAnalysisButton = QPushButton(self.parameterPanel)
        self.cancelAnalysisButton.setObjectName(u"cancelAnalysisButton")
        self.cancelAnalysisButton.setEnabled(False)

        self.analysisActionLayout.addWidget(self.cancelAnalysisButton)


        self.parameterLayout.addLayout(self.analysisActionLayout)

        self.mainSplitter.addWidget(self.parameterPanel)
        self.workspacePanel = QWidget(self.mainSplitter)
        self.workspacePanel.setObjectName(u"workspacePanel")
        self.workspaceLayout = QVBoxLayout(self.workspacePanel)
        self.workspaceLayout.setSpacing(10)
        self.workspaceLayout.setObjectName(u"workspaceLayout")
        self.workspaceLayout.setContentsMargins(12, 12, 12, 12)
        self.taskHeader = QFrame(self.workspacePanel)
        self.taskHeader.setObjectName(u"taskHeader")
        self.taskHeader.setFrameShape(QFrame.Shape.StyledPanel)
        self.taskHeader.setFrameShadow(QFrame.Shadow.Raised)
        self.taskHeaderLayout = QHBoxLayout(self.taskHeader)
        self.taskHeaderLayout.setObjectName(u"taskHeaderLayout")
        self.taskHeaderLayout.setContentsMargins(10, 8, 10, 8)
        self.currentTaskLabel = QLabel(self.taskHeader)
        self.currentTaskLabel.setObjectName(u"currentTaskLabel")

        self.taskHeaderLayout.addWidget(self.currentTaskLabel)

        self.recordCountLabel = QLabel(self.taskHeader)
        self.recordCountLabel.setObjectName(u"recordCountLabel")

        self.taskHeaderLayout.addWidget(self.recordCountLabel)

        self.taskHeaderHorizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.taskHeaderLayout.addItem(self.taskHeaderHorizontalSpacer)

        self.refreshButton = QPushButton(self.taskHeader)
        self.refreshButton.setObjectName(u"refreshButton")

        self.taskHeaderLayout.addWidget(self.refreshButton)

        self.exportButton = QPushButton(self.taskHeader)
        self.exportButton.setObjectName(u"exportButton")
        self.exportButton.setEnabled(False)

        self.taskHeaderLayout.addWidget(self.exportButton)


        self.workspaceLayout.addWidget(self.taskHeader)

        self.resultTabWidget = QTabWidget(self.workspacePanel)
        self.resultTabWidget.setObjectName(u"resultTabWidget")
        self.resultTab = QWidget()
        self.resultTab.setObjectName(u"resultTab")
        self.resultTabLayout = QVBoxLayout(self.resultTab)
        self.resultTabLayout.setObjectName(u"resultTabLayout")
        self.resultTabLayout.setContentsMargins(0, 0, 0, 0)
        self.relationResultStackedWidget = QStackedWidget(self.resultTab)
        self.relationResultStackedWidget.setObjectName(u"relationResultStackedWidget")
        self.emptyPage = QWidget()
        self.emptyPage.setObjectName(u"emptyPage")
        self.emptyPageLayout = QVBoxLayout(self.emptyPage)
        self.emptyPageLayout.setObjectName(u"emptyPageLayout")
        self.emptyPageTopSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.emptyPageLayout.addItem(self.emptyPageTopSpacer)

        self.relationEmptyTitleLabel = QLabel(self.emptyPage)
        self.relationEmptyTitleLabel.setObjectName(u"relationEmptyTitleLabel")
        self.relationEmptyTitleLabel.setFont(font)
        self.relationEmptyTitleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.emptyPageLayout.addWidget(self.relationEmptyTitleLabel)

        self.relationEmptyHintLabel = QLabel(self.emptyPage)
        self.relationEmptyHintLabel.setObjectName(u"relationEmptyHintLabel")
        self.relationEmptyHintLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.emptyPageLayout.addWidget(self.relationEmptyHintLabel)

        self.emptyPageBottomSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.emptyPageLayout.addItem(self.emptyPageBottomSpacer)

        self.relationResultStackedWidget.addWidget(self.emptyPage)
        self.loadingPage = QWidget()
        self.loadingPage.setObjectName(u"loadingPage")
        self.loadingPageLayout = QVBoxLayout(self.loadingPage)
        self.loadingPageLayout.setObjectName(u"loadingPageLayout")
        self.loadingPageTopSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.loadingPageLayout.addItem(self.loadingPageTopSpacer)

        self.relationLoadingLabel = QLabel(self.loadingPage)
        self.relationLoadingLabel.setObjectName(u"relationLoadingLabel")
        self.relationLoadingLabel.setFont(font)
        self.relationLoadingLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.loadingPageLayout.addWidget(self.relationLoadingLabel)

        self.loadingPageBottomSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.loadingPageLayout.addItem(self.loadingPageBottomSpacer)

        self.relationResultStackedWidget.addWidget(self.loadingPage)
        self.resultPage = QWidget()
        self.resultPage.setObjectName(u"resultPage")
        self.resultPageLayout = QVBoxLayout(self.resultPage)
        self.resultPageLayout.setObjectName(u"resultPageLayout")
        self.resultPageLayout.setContentsMargins(0, 0, 0, 0)
        self.relationResultToolbar = QWidget(self.resultPage)
        self.relationResultToolbar.setObjectName(u"relationResultToolbar")
        self.relationResultToolbarLayout = QHBoxLayout(self.relationResultToolbar)
        self.relationResultToolbarLayout.setObjectName(u"relationResultToolbarLayout")
        self.relationResultToolbarLayout.setContentsMargins(12, 8, 12, 4)
        self.relationVariationFilterArea = QWidget(self.relationResultToolbar)
        self.relationVariationFilterArea.setObjectName(u"relationVariationFilterArea")
        self.relationVariationFilterAreaLayout = QHBoxLayout(self.relationVariationFilterArea)
        self.relationVariationFilterAreaLayout.setObjectName(u"relationVariationFilterAreaLayout")
        self.relationVariationFilterAreaLayout.setContentsMargins(0, 0, 0, 0)
        self.relationVariationFiltersLayout = QHBoxLayout()
        self.relationVariationFiltersLayout.setSpacing(8)
        self.relationVariationFiltersLayout.setObjectName(u"relationVariationFiltersLayout")
        self.relationVariationFiltersLayout.setContentsMargins(0, 0, 0, 0)

        self.relationVariationFilterAreaLayout.addLayout(self.relationVariationFiltersLayout)

        self.resetRelationFiltersButton = QToolButton(self.relationVariationFilterArea)
        self.resetRelationFiltersButton.setObjectName(u"resetRelationFiltersButton")

        self.relationVariationFilterAreaLayout.addWidget(self.resetRelationFiltersButton)


        self.relationResultToolbarLayout.addWidget(self.relationVariationFilterArea)

        self.relationResultToolbarSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.relationResultToolbarLayout.addItem(self.relationResultToolbarSpacer)

        self.selectVisibleRelationButton = QToolButton(self.relationResultToolbar)
        self.selectVisibleRelationButton.setObjectName(u"selectVisibleRelationButton")

        self.relationResultToolbarLayout.addWidget(self.selectVisibleRelationButton)

        self.selectedRelationCountLabel = QLabel(self.relationResultToolbar)
        self.selectedRelationCountLabel.setObjectName(u"selectedRelationCountLabel")

        self.relationResultToolbarLayout.addWidget(self.selectedRelationCountLabel)


        self.resultPageLayout.addWidget(self.relationResultToolbar)

        self.relationResultScrollArea = QScrollArea(self.resultPage)
        self.relationResultScrollArea.setObjectName(u"relationResultScrollArea")
        self.relationResultScrollArea.setFrameShape(QFrame.Shape.NoFrame)
        self.relationResultScrollArea.setWidgetResizable(True)
        self.relationResultScrollContent = QWidget()
        self.relationResultScrollContent.setObjectName(u"relationResultScrollContent")
        self.relationResultScrollContent.setGeometry(QRect(0, 0, 760, 500))
        self.relationProductGridLayout = QGridLayout(self.relationResultScrollContent)
        self.relationProductGridLayout.setObjectName(u"relationProductGridLayout")
        self.relationProductGridLayout.setHorizontalSpacing(12)
        self.relationProductGridLayout.setVerticalSpacing(12)
        self.relationProductGridLayout.setContentsMargins(12, 12, 12, 12)
        self.relationResultScrollArea.setWidget(self.relationResultScrollContent)

        self.resultPageLayout.addWidget(self.relationResultScrollArea)

        self.relationResultStackedWidget.addWidget(self.resultPage)

        self.resultTabLayout.addWidget(self.relationResultStackedWidget)

        self.resultTabWidget.addTab(self.resultTab, "")
        self.dataTab = QWidget()
        self.dataTab.setObjectName(u"dataTab")
        self.dataLayout = QVBoxLayout(self.dataTab)
        self.dataLayout.setObjectName(u"dataLayout")
        self.dataToolbarLayout = QHBoxLayout()
        self.dataToolbarLayout.setObjectName(u"dataToolbarLayout")
        self.dataToolbarLayout.setContentsMargins(0, 0, 0, 0)
        self.tableSearchLineEdit = QLineEdit(self.dataTab)
        self.tableSearchLineEdit.setObjectName(u"tableSearchLineEdit")
        self.tableSearchLineEdit.setClearButtonEnabled(True)

        self.dataToolbarLayout.addWidget(self.tableSearchLineEdit)

        self.resetFilterButton = QToolButton(self.dataTab)
        self.resetFilterButton.setObjectName(u"resetFilterButton")

        self.dataToolbarLayout.addWidget(self.resetFilterButton)


        self.dataLayout.addLayout(self.dataToolbarLayout)

        self.resultTableView = QTableView(self.dataTab)
        self.resultTableView.setObjectName(u"resultTableView")

        self.dataLayout.addWidget(self.resultTableView)

        self.resultTabWidget.addTab(self.dataTab, "")

        self.workspaceLayout.addWidget(self.resultTabWidget)

        self.mainSplitter.addWidget(self.workspacePanel)

        self.centralLayout.addWidget(self.mainSplitter)

        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 1200, 33))
        self.menu = QMenu(self.menubar)
        self.menu.setObjectName(u"menu")
        self.menu_2 = QMenu(self.menubar)
        self.menu_2.setObjectName(u"menu_2")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.menubar.addAction(self.menu_2.menuAction())
        self.menubar.addAction(self.menu.menuAction())
        self.menu.addAction(self.actionAI)
        self.menu.addAction(self.action)
        self.menu.addAction(self.action_2)
        self.menu_2.addAction(self.action_3)
        self.menu_2.addAction(self.action_4)

        self.retranslateUi(MainWindow)

        self.resultTabWidget.setCurrentIndex(0)
        self.relationResultStackedWidget.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"Flow Analysis", None))
        self.actionAI.setText(QCoreApplication.translate("MainWindow", u"AI\u8bbe\u7f6e", None))
        self.action.setText(QCoreApplication.translate("MainWindow", u"\u6570\u636e\u5e93\u8bbe\u7f6e", None))
        self.action_2.setText(QCoreApplication.translate("MainWindow", u"\u5356\u5bb6\u7cbe\u7075\u8bbe\u7f6e", None))
        self.action_3.setText(QCoreApplication.translate("MainWindow", u"\u6807\u7b7e\u7ba1\u7406", None))
        self.action_4.setText(QCoreApplication.translate("MainWindow", u"\u5f52\u4e00\u5316\u7ba1\u7406", None))
        self.parameterTitleLabel.setText(QCoreApplication.translate("MainWindow", u"\u5206\u6790\u914d\u7f6e", None))
        self.asinLabel.setText(QCoreApplication.translate("MainWindow", u"ASIN", None))
        self.asinLineEdit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u8f93\u5165 ASIN", None))
        self.asinQueryButton.setText(QCoreApplication.translate("MainWindow", u"\u67e5\u8be2", None))
        self.dateRangeLabel.setText(QCoreApplication.translate("MainWindow", u"\u65f6\u95f4\u8303\u56f4", None))
        self.analysisTypeLabel.setText(QCoreApplication.translate("MainWindow", u"\u6253\u6807\u54c1\u7c7b", None))
        self.analysisTypeComboBox.setItemText(0, QCoreApplication.translate("MainWindow", u"Pillow", None))
        self.analysisTypeComboBox.setItemText(1, QCoreApplication.translate("MainWindow", u"Stuffed Animals", None))

        self.startAnalysisButton.setText(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u5206\u6790", None))
        self.cancelAnalysisButton.setText(QCoreApplication.translate("MainWindow", u"\u53d6\u6d88", None))
        self.currentTaskLabel.setText(QCoreApplication.translate("MainWindow", u"\u5c1a\u672a\u5f00\u59cb\u5206\u6790", None))
        self.recordCountLabel.setText(QCoreApplication.translate("MainWindow", u"0 \u6761\u6570\u636e", None))
        self.refreshButton.setText(QCoreApplication.translate("MainWindow", u"\u5237\u65b0", None))
        self.exportButton.setText(QCoreApplication.translate("MainWindow", u"\u5bfc\u51fa Excel", None))
        self.relationEmptyTitleLabel.setText(QCoreApplication.translate("MainWindow", u"\u6682\u65e0\u5173\u8054 ASIN", None))
        self.relationEmptyHintLabel.setText(QCoreApplication.translate("MainWindow", u"\u8f93\u5165 ASIN \u540e\u70b9\u51fb\u67e5\u8be2", None))
        self.relationLoadingLabel.setText(QCoreApplication.translate("MainWindow", u"\u6b63\u5728\u67e5\u8be2\u5173\u8054 ASIN...", None))
        self.resetRelationFiltersButton.setText(QCoreApplication.translate("MainWindow", u"\u91cd\u7f6e\u7b5b\u9009", None))
        self.selectVisibleRelationButton.setText(QCoreApplication.translate("MainWindow", u"\u5168\u9009", None))
        self.selectedRelationCountLabel.setText(QCoreApplication.translate("MainWindow", u"\u5df2\u9009\u62e9 0 / 0", None))
        self.resultTabWidget.setTabText(self.resultTabWidget.indexOf(self.resultTab), QCoreApplication.translate("MainWindow", u"\u7ed3\u679c", None))
        self.tableSearchLineEdit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u641c\u7d22 ASIN / \u6807\u9898 / \u5173\u952e\u8bcd", None))
        self.resetFilterButton.setText(QCoreApplication.translate("MainWindow", u"\u91cd\u7f6e", None))
        self.resultTabWidget.setTabText(self.resultTabWidget.indexOf(self.dataTab), QCoreApplication.translate("MainWindow", u"\u6570\u636e", None))
        self.menu.setTitle(QCoreApplication.translate("MainWindow", u"\u8bbe\u7f6e", None))
        self.menu_2.setTitle(QCoreApplication.translate("MainWindow", u"\u6570\u636e\u5e93", None))
    # retranslateUi


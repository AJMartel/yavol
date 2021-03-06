__author__ = 'Jaroslav Brtan'
__version__ = '0.0.1'

'''
YaVol - GUI for volatility framework and yara scanner
Copyright (C) 2015  Jaroslav Brtan

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

from PyQt4.QtCore import QObject, QThread, pyqtSignal, SIGNAL, QCoreApplication, QString, QVariant, \
                            QSettings, Qt
from PyQt4.QtGui import QMainWindow, QTabWidget, QDockWidget, QListWidget, QLabel, QFrame, QKeySequence, \
                        QWidget, QAction, QIcon, QFileDialog, QMessageBox, QHBoxLayout, QTextEdit, QTableWidget, \
                        QComboBox, QTableWidgetItem, QAbstractItemView, QGridLayout, QSpacerItem, QSizePolicy, \
                        QMenu, QApplication

from os import remove, path, makedirs

import resources

from functools import partial

import re

import Queue as queue

import webbrowser

import volmodule

import pprint

import settingsDlg
import yarascanDlg
import dbmodule
import yarascanTreeView

from shutil import copyfile

from time import time


def logger(func):
    def forward(*args, **kwargs):
        print "Arguments were: %s, %s" % (args, kwargs)
        return func(*args, **kwargs)

    return forward

class ResultObj(QObject):
    def __init__(self, moduleName, retValObj):
        self.moduleName = moduleName
        self.retValObj = retValObj

class QueueObj(QObject):
    def __init__(self, moduleName, filename, profile, yara_rule_name, output_path, \
                 pid=None, dump_dir="dump/"):
        self.moduleName = moduleName
        self.filename = filename
        self.profile = profile
        self.yara_rule = yara_rule_name
        self.output_path = output_path
        self.pid = pid
        self.dump_dir = dump_dir

class Worker(QThread):
    finished = pyqtSignal(object)

    def __init__(self, queue, callback, parent=None):
        QThread.__init__(self, parent)
        self.queue = queue
        self.finished.connect(callback)

    def __del__(self):
        self.exiting = True
        self.wait()

    def run(self):

        while True:
            query = self.queue.get()
            if query is None:  # None means exit
                print("Shutting down thread")
                return
            self.nigga(query)

    def nigga(self, query):

        moduleName = query.moduleName
        volatilityInstance = volmodule.VolatilityFunctions(query)
        retObj = volatilityInstance.runModule(moduleName)

        self.finished.emit(ResultObj(moduleName, retObj))

class TableWidget(QTableWidget):

    def __init__(self, window_class, data, tabName, *args):
        QTableWidget.__init__(self, *args)
        self.data = data
        self.setmydata()
        self.resizeColumnsToContents()
        self.resizeRowsToContents()
        self.tabName = tabName
        self.window = window_class  # we need pointer to this class to add jobs to the queue

    def setmydata(self):

        horHeaders = []
        for n, key in enumerate(self.data.keys()):
            horHeaders.append(key)
            for m, item in enumerate(self.data[key]):
                newitem = QTableWidgetItem(item)
                self.setItem(m, n, newitem)
        self.setHorizontalHeaderLabels(horHeaders)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)

    def contextMenuEvent(self, event):

        menu = QMenu(self)
        clipboardAction = menu.addAction("Copy to clipboard")
        googleSearchAction = menu.addAction("Search with Google")

        #if tab name is pslist, psscan, psxview
        dumpProcMem = None
        if self.tabName == 'pslist' or self.tabName == 'psscan' or self.tabName == 'psxview':
            dumpProcMem = menu.addAction("Dump proc exec")

        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == clipboardAction:
            cb = QApplication.clipboard()
            cb.clear(mode=cb.Clipboard )

            for item in self.selectedItems():
                cb.setText(item.text(), mode=cb.Clipboard)

        if action == googleSearchAction:
            items = self.selectedItems()
            search_string = "https://www.google.com/search?q=" + items[0].text()
            webbrowser.open(search_string)

        if action == dumpProcMem:
            rows = sorted(set(index.row() for index in
                      self.selectedIndexes()))

            headercount = self.columnCount()
            # for each selected row get me the PID of the process
            for row in rows:
                #print('Row %d is selected' % row)

                #get pids from those rows
                for x in range(0, headercount, 1):
                    headertext = self.horizontalHeaderItem(x).text()
                    if headertext == 'PID':
                        matchcol = x
                        break

                pid = self.item(row,matchcol).text()
                #call procdump module with the PID
                dump_dir =  self.window.getParticularSettingValue('dump_dir')
                self.window.thread_process('procdump', self.window.fullpath, self.window.profile, None, \
                                           self.window.output_path, pid=pid, dump_dir=dump_dir)

class Window(QMainWindow):
    def __init__(self, parent=None):
        super(Window, self).__init__(parent)
        self.tabWidget = QTabWidget()
        self.tabWidget.setTabsClosable(True)
        self.tabWidget.tabCloseRequested.connect(self.closeTab)
        self.setCentralWidget(self.tabWidget)
        self.dirty = False
        self.filename = None
        self.dir = None
        self.fullpath = None
        self.dump_dir = None
        self.file_size = 0
        self.output_path = "tmp/output.sqlite"
        self.user_db_path = ""
        self.volatilityInstance = None
        self.profile = "Use Imageinfo"
        self.create_widgets()
        self.create_actions()
        self.settings = self.loadAppSettings()
        self.setWindowTitle("YaVol")
        # self.updateFileMenu()
        self.imageinfoShown = False
        self.path_to_yara_rule = None
        self.yarascan_queue_size = 0  #used to determine when we finished scanning

    def create_widgets(self):

        logDockWidget = QDockWidget("Log", self)
        logDockWidget.setObjectName("LogDockWidget")
        logDockWidget.setAllowedAreas(Qt.LeftDockWidgetArea |
                                      Qt.RightDockWidgetArea)
        self.listWidget = QListWidget()
        logDockWidget.setWidget(self.listWidget)
        self.addDockWidget(Qt.RightDockWidgetArea, logDockWidget)

        self.sizeLabel = QLabel()
        self.sizeLabel.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.status = self.statusBar()
        self.status.setSizeGripEnabled(False)
        self.status.addPermanentWidget(self.sizeLabel)
        self.status.showMessage("Ready", 5000)

    def create_actions(self):

        fileNewAction = self.createAction("&New Analysis", self.fileOpen,
                                          QKeySequence.New, "filenew", "Analyse an image file")

        fileOpenAction = self.createAction("&Open Analysis", self.analysisOpen,
                                           QKeySequence.Open, "fileopen", "Restore previous analysis")

        fileSaveAction = self.createAction("&Save Analysis", partial(self.fileSave, False),
                                           QKeySequence.Save, "filesave", "Save analysis")

        fileExitAction = self.createAction("&Exit", self.appExit,
                                           QKeySequence.Close, None, "Exit YaVol")

        editSettingsAction = self.createAction("&Settings", self.showSettingsDialog,
                                               QKeySequence.Preferences, "settings", "YaVol Settings")

        volPslistAction = self.createAction("pslist", partial(self.actionModule, 'pslist'),
                                            None, "pslist", "List of running processes")

        volPsscanAction = self.createAction("psscan", partial(self.actionModule, 'psscan'),
                                            None, "psscan", "List of running processes")

        volDlllistAction = self.createAction("dlllist", partial(self.actionModule, 'dlllist'),
                                             None, "dlllist", "List of loaded DLLs")

        volHandlesAction = self.createAction("handles", partial(self.actionModule, 'handles'),
                                             None, "handles", "List of open handles")

        volGetsidsAction = self.createAction("getsids", partial(self.actionModule, 'getsids'),
                                             None, "getsids", "View SIDs associated with a process")

        volPrivsAction = self.createAction("privs", partial(self.actionModule, 'privs'),
                                           None, "privs", "Shows which process privileges are present")

        volVerinfoAction = self.createAction("verinfo", partial(self.actionModule, 'verinfo'),
                                             None, "verinfo", "Display the version information embedded in PE files")

        volEnumfuncAction = self.createAction("enumfunc", partial(self.actionModule, 'enumfunc'),
                                              None, "enumfunc", "Enumerates imported&exported functions from processes")

        volConnectionsAction = self.createAction("connections", partial(self.actionModule, 'connections'),
                                                 None, "connections", "List of network connections")

        volConnscanAction = self.createAction("connscan", partial(self.actionModule, 'connscan'),
                                              None, "connscan", "List of network connections")

        volSocketsAction = self.createAction("sockets", partial(self.actionModule, 'sockets'),
                                             None, "sockets", "Description missing")

        volSockscanAction = self.createAction("sockscan", partial(self.actionModule, 'sockscan'),
                                              None, "sockscan", "Description missing")

        volNetscanAction = self.createAction("netscan", partial(self.actionModule, 'netscan'),
                                             None, "netscan", "Description missing")

        volMalfindAction = self.createAction("malfind", partial(self.actionModule, 'malfind'),
                                             None, "malfind", "Description missing")

        volSvcscanAction = self.createAction("svcscan", partial(self.actionModule, 'svcscan'),
                                             None, "svcscan", "Description missing")

        volPsxviewAction = self.createAction("psxview", partial(self.actionModule, 'psxview'),
                                             None, "psxview", "Description missing")

        #yaScanallAction = self.createAction("scan image", partial(self.actionModule, 'yarascan'),
        #                                    None, None, "Scan whole image with yara")
        yaScanallAction = self.createAction("scan image", self.showYaraScanDialog,
                                            None, None, "Scan whole image with yara")

        helpAboutAction = self.createAction("about", self.showAboutInfo,
                                            None, None, "Who the hell created this crap?")

        fileMenu = self.menuBar().addMenu("&File")
        self.addActions(fileMenu, (fileNewAction, fileOpenAction, fileSaveAction, fileExitAction))

        editMenu = self.menuBar().addMenu("&Edit")
        self.addActions(editMenu, (editSettingsAction,))

        volMenu = self.menuBar().addMenu("&Volatility")
        volMenuProcesses = volMenu.addMenu("Proc&DLLs")
        self.addActions(volMenuProcesses, (volPslistAction, volPsscanAction, volDlllistAction, volHandlesAction,
                                           volGetsidsAction, volPrivsAction, volVerinfoAction, volEnumfuncAction))

        volMenuNetwork = volMenu.addMenu("Network")
        self.addActions(volMenuNetwork, (volConnectionsAction, volConnscanAction, volSocketsAction, volSockscanAction,
                                         volNetscanAction))

        volMenuMalware = volMenu.addMenu("Malware")
        self.addActions(volMenuMalware, (volMalfindAction, volSvcscanAction, volPsxviewAction))

        yaraMenu = self.menuBar().addMenu("&Yara")
        self.addActions(yaraMenu, (yaScanallAction,))

        helpMenu = self.menuBar().addMenu("&Help")
        self.addActions(helpMenu, (helpAboutAction,))

        # toolbar
        fileToolbar = self.addToolBar("File")
        fileToolbar.setObjectName("FileToolBar")
        self.addActions(fileToolbar, (fileNewAction, fileOpenAction, fileSaveAction,))

    def createAction(self, text, slot=None, shortcut=None, icon=None,
                     tip=None, checkable=False, signal="triggered()"):
        '''
        helper method for setting up actions
        '''
        action = QAction(text, self)
        if icon is not None:
            action.setIcon(QIcon(":/%s.png" % icon))
        if shortcut is not None:
            action.setShortcut(shortcut)
        if tip is not None:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        if slot is not None:
            self.connect(action, SIGNAL(signal), slot)
        if checkable:
            action.setCheckable(True)
        return action

    def addActions(self, target, actions):
        for action in actions:
            if action is None:
                target.addSeparator()
            else:
                target.addAction(action)

    def loadAppSettings(self):
        settings = QSettings()
        ''' Since we passed no arguments, the names held by the application object
        are used to locate the settings information
        '''
        self.recentFiles = settings.value("RecentFiles").toStringList()
        '''method always returns a QVariant, so we must convert it to the data type we are expecting.'''
        self.restoreGeometry(
            settings.value("MainWindow/Geometry").toByteArray())
        self.restoreState(settings.value("MainWindow/State").toByteArray())

        # First app start only, set the defaults
        if settings.value('dictionary') == None:
            settings.setValue('dictionary', {'yara': {'rules_dir': {'path': '~/git/yavol_gt/yara'}},
                                             'dump_dir': 'dump/',
                                             'bar': 2})

        return settings

    def showSettingsDialog(self):
        dialog = settingsDlg.settingsDlg(self.settings, self)
        if dialog.exec_():
            pass

    def getParticularSettingValue(self, keyword):
        #expects key for searching in settings dict, returns associated value
        settings_dict = self.settings.value('dictionary').toPyObject()

        if keyword == 'yara_rules_dir':
            return str(settings_dict[QString('yara')][QString('rules_dir')][QString('path')])

        elif keyword == 'dump_dir':
            return str(settings_dict[QString('dump_dir')])
        else:
            return False

    def showYaraScanDialog(self):

        #check if we got a memory image file opened
        if self.filename:

            #TODO: create a method that will return particular values from the QSettings object
            #settings = QSettings()
            #settings_dict = self.settings.value('dictionary').toPyObject()

            #path_to_rules = settings_dict[QString('yara')][QString('rules_dir')][QString('path')]

            path_to_rules = self.getParticularSettingValue('yara_rules_dir')

            dialog = yarascanDlg.yarascanDlg(path_to_rules)
            if dialog.exec_():
                #check if the returned array of signatures is empty
                #and run the scan
                #DEBUG
                #pprint.pprint(dialog.selected_rules)

                if len(dialog.selected_rules) > 0:

                    self.volatilityInstance = None  #test
                    self.yarascan_queue_size = len(dialog.selected_rules)
                    for rule in dialog.selected_rules:
                        self.path_to_yara_rule = str(path_to_rules + '/' + rule + '.yar')
                        #pprint.pprint(self.path_to_yara_rule)
                        self.actionModule('yarascan')
        else:
            #we dont have a memory image file opened
            self.showWarningInfo('Cannot process', 'No image file specified\n Open a memory image file first.')

    def closeEvent(self, event):
        if self.okToContinue():

            #delete temp db file (/tmp/output.sqlite)
            if path.isfile('tmp/output.sqlite'):
                remove('tmp/output.sqlite')

            # self.settings = QSettings()
            filename = (QVariant(QString(self.filename))
                        if self.filename is not None else QVariant())
            self.settings.setValue("LastFile", filename)
            recentFiles = (QVariant(self.recentFiles)
                           if self.recentFiles else QVariant())
            self.settings.setValue("RecentFiles", recentFiles)
            self.settings.setValue("MainWindow/Geometry", QVariant(
                self.saveGeometry()))
            self.settings.setValue("MainWindow/State", QVariant(
                self.saveState()))
            del self.settings
        else:
            event.ignore()

    def okToContinue(self):
        if self.dirty:
            reply = QMessageBox.question(self,
                                         "yavol - Unsaved Changes",
                                         "Save unsaved changes?",
                                         QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if reply == QMessageBox.Cancel:
                return False
            elif reply == QMessageBox.Yes:
                return self.fileSave(True)
        return True

    def fileOpen(self):
        if not self.okToContinue():
            return
        wdir = path.dirname(self.filename) if self.filename is not None else "."
        formats = ["*.img", "*.dmp"]
        fname = unicode(QFileDialog.getOpenFileName(self, "YaVol - Choose Image", wdir,
                                                    "Memory files (%s)" % " ".join(formats)))
        if fname:

            #check if the tmp dir exists
            if not path.exists('tmp'):
                makedirs('tmp')

            #check if we already got tmp file created (user opened something before)
            if path.isfile('tmp/output.sqlite'):
                remove('tmp/output.sqlite')
                self.displayInLog("temp file deleted!")

            #get image file size
            self.file_size = path.getsize(fname)


            self.filename = path.basename(fname)
            self.dir = path.dirname(fname)
            self.fullpath = fname

            #store image data
            stored = self.storeImageData()

            if stored:
                self.displayInLog("storeImageData success")
            else:
                self.displayInLog("storeImageData failed")

            self.showImageTab()

    def analysisOpen(self):

        fname = unicode(QFileDialog.getOpenFileName(self, "YaVol - Choose analysis file", '',
                                                    "Analysis files (*.sqlite)"))
        if fname:
            self.displayInLog("Opening db file: " + unicode(fname))
            db = dbmodule.sqlitequery('getProfile', fname)
            result = db.getProfileInfo()
            if result:
                #pprint.pprint(result)
                #check if the memory file is still in the same location
                image_full_path = result[3] + '/' + result[1]
                #print image_full_path
                if path.isfile(image_full_path):
                    #file is still there, set the variables filename, dir, fullpath
                    self.filename = result[1]
                    self.dir = result[3]
                    self.fullpath = image_full_path
                    # set the last used profile
                    self.profile = result[4]
                    # show image tab
                    self.showImageTab()

                    #copy sqlite file to the tmp folder
                    copyfile(fname,self.output_path)
                    self.dirty = False
                else:
                    # memory image file is no longer in the same location, show warning
                    #TODO:user should be provided with an option to specify new path
                    self.showWarningInfo('Image file not found', 'Memory image file was not found!')

                #print image_full_path

    def fileSave(self, exit):

        #check the dirty flag
        if self.dirty == False:
            #show dialog that there is nothing to be saved
            self.showWarningInfo("Save analysis", "There is nothing to be saved")
        else:
            # show the save dialog

            filename, filter = QFileDialog.getSaveFileNameAndFilter(self, 'Save file', '', "Database file (*.sqlite)")
            if filename !="":
                # copy the /tmp/output.sqlite to the location selected by the user
                strFileName = str(filename)
                strFilter = str(filter)
                dst = ""
                src = ""
                if strFilter.endswith('.sqlite)'):
                    src = self.output_path
                    if strFileName.endswith('.sqlite'):
                        dst = strFileName
                    else:
                        dst = strFileName + '.sqlite'

                    try:
                        #print dst
                        copyfile(src, dst)
                        #changes were stored, unset the dirty flag
                        self.dirty = False

                        #write to analysisFile


                        if exit:
                            remove(src)
                            #returning true to the caller (okToContinue) will cause exit
                            return True
                        else:
                            remove(src)
                            self.output_path = dst
                            self.dirty = False

                    except IOError as e:
                        #print "I/O error({0}): {1}".format(e.errno, e.strerror)
                        self.showWarningInfo('File saving failed', e.strerror)
                    except ValueError:
                        print "Could not convert data to an integer."
                    except:
                        print "Unexpected error:", sys.exc_info()[0]
                        raise

    def appExit(self):
        #check the status of the dirty flag
        if self.dirty == False:

            #clean the temp folder
            if path.isfile('tmp/output.sqlite'):
                remove('tmp/output.sqlite')
            #and quit
            QCoreApplication.instance().quit()
        else:
            if self.okToContinue():
                if path.isfile('tmp/output.sqlite'):
                    remove('tmp/output.sqlite')
                QCoreApplication.instance().quit()

    def storeProfile(self, profile):
        # If volatility class was called with a profile value
        # instance was stored in volatilityInstance. In case that user wants to use another profile,
        # previous instance must be dropped
        if self.volatilityInstance:
            self.volatilityInstance = None
        self.profile = unicode(profile)
        self.displayInLog("Profile changed: " + unicode(profile))

        db = dbmodule.sqlitequery('updateProfile', self.output_path)
        db.updateProfileInfo(self.profile)
        self.dirty = True

    def storeImageData(self):
        #get current datetime
        unix_time = int(time())
        db = dbmodule.sqlitequery('fileOpen', self.output_path)
        status = db.storeImageData(imgName=self.filename, imgSize=self.file_size, imgPath=self.dir, time=unix_time)
        return status

    def handle_result(self, result):
        # this method is a callback which should
        # process the data from the worker thread
        moduleName = result.moduleName

        #if the result comes from yarascan,
        # 1) check if the value of the yara_scan_queue_size > 1
        # 1a) lower its size by one
        # 1aa) send the result to buffer
        # 2) if the value of yara_scan_queue_size after the substraction is eq 0
        #    send the buffer to processing
        if moduleName == 'yarascan': # yarascan module output will be taken special care
            if self.yarascan_queue_size > 1:
                self.yarascan_queue_size -= 1
            else:
                #memory image scan was finished with all selected rules
                # get the data from db and show it to user in a treeview
                self.yarascan_queue_size = 0

                db = dbmodule.sqlitequery(moduleName, self.output_path)
                data = db.getData()

                #if there is no hit then returned dict looks like this:
                #OrderedDict([('Rule', []), ('Owner', []), ('Address', []), ('Data', [])])
                if data['Rule']:
                    self.addToTab(moduleName, 'tree', data)
                    #TODO: maybe we would like to store previous scan results..
                    # drop the yarascan table
                    db.dropYaraScanTable('YaraScan')

                else:
                    self.addToTab(moduleName, 'list', 'No hit!')

        elif moduleName == 'procdump': # we've dumped some content, no db entry made
            #I should check for the return value of the module and handle error properly
            self.displayInLog("procdump finished")

        else:   #output of the rest of the modules will be shown right away in a tab
            # textVal is used only with imageinfo module
            # (and some others that don't write to sqlite)
            # If it is defined display it in a new tab
            if result.retValObj.textVal:
                self.addToTab(moduleName, 'list', result.retValObj.textVal)
            else:
                # textVal is not defined, this means data was stored in DB
                # we need to get them
                db = dbmodule.sqlitequery(moduleName, self.output_path)
                data = db.getData()

                #TODO: offsets are stored in decimal for pslist,psscan, psxview, dlllist, handles,netscan,svcan, malfind
                #convert offsets from decimal to hexa
                modified_data = self.convertOffsets(moduleName, data)

                self.addToTab(moduleName, 'table', modified_data)

    def convertOffsets(self, moduleName, data):

        if moduleName == 'pslist':
            offsets = data['Offset(V)']
            self.listToHex(offsets)
        elif moduleName == 'psscan':
            offsets = data['Offset(P)']
            self.listToHex(offsets)
        elif moduleName == 'dlllist':
            offsets = data['Base']
            self.listToHex(offsets)
        elif moduleName == 'handles':
            offsets = data['Offset(V)']
            self.listToHex(offsets)
        elif moduleName == 'netscan':
            offsets = data['Offset(P)']
            self.listToHex(offsets)
        elif moduleName == 'psxview':
            offsets = data['Offset(P)']
            self.listToHex(offsets)
        elif moduleName == 'svcscan':
            offsets = data['Offset']
            self.listToHex(offsets)
        #malfind will require some more work
        elif moduleName == 'malfind':
            offsets = data['Address']
            self.listToHex(offsets)

        return data

    def listToHex(self, list):

        for index, value in enumerate(list):
                number = hex(int(value))
                list[index] = number

    def thread_process(self, moduleName, filename, profile, yara_rule_path, output_path, pid=None, dump_dir="dump/"):
        MAX_CORES = 2
        self.queue = queue.Queue()
        self.threads = []
        for i in range(MAX_CORES):
            thread = Worker(self.queue, self.handle_result)
            self.threads.append(thread)
            thread.start()

        query = QueueObj(moduleName, filename, profile, yara_rule_path, output_path, pid, dump_dir)

        self.queue.put(query)

        for _ in range(MAX_CORES):  # Tell the workers to shut down
            self.queue.put(None)

    def actionModule(self, moduleName):

        #check if we got a memory image file specified first
        if self.filename:

            # check if the selected image profile supports this module
            compatibilityCheck = re.match('Vista|Win2008|Win7', self.profile, flags=0)

            if moduleName in ['connections', 'connscan', 'sockscan', 'sockets']:
                if compatibilityCheck:
                    self.displayInLog("Error: This module can't be use with this profile")
                    return False


            # check if we got an open table with this module output
            if moduleName != 'yarascan':
                #print self.tabWidget.count()
                for i in range(self.tabWidget.count()):
                    if self.tabWidget.tabText(i) == moduleName.lower():
                        #set focus on the tab
                        self.tabWidget.setCurrentIndex(i)
                        return True

            #check if our db already contains table with this module output
            if self.checkForTable(moduleName) and moduleName != 'yarascan':
                self.displayInLog("Info: We already have this module output in db!")
                #get the data and display it
                db = dbmodule.sqlitequery(moduleName, self.output_path)
                data = db.getData()

                #convert offsets from dec to hex
                modified_data = self.convertOffsets(moduleName, data)

                self.addToTab(moduleName, 'table', modified_data)

            else:
                #we dont have this output in our db
                self.status.showMessage("Creating %s output" % moduleName, 5000)
                self.displayInLog("%s with a profile called!" % moduleName)
                # TODO: remove check for volatilityInstance, this is no longer in use!!!
                #if self.volatilityInstance != None:
                #    self.displayInLog("Info: Volatility instance found")
                if self.path_to_yara_rule:
                    self.thread_process(moduleName, self.fullpath, self.profile,
                                            self.path_to_yara_rule, self.output_path)
                else:
                    self.thread_process(moduleName, self.fullpath, self.profile,
                                            None, self.output_path)

                self.dirty = True

        else:
            # we dont have an image file opened
            self.showWarningInfo('Cannot process', 'No image file specified\n Open a memory image file first.')

    def addTabFnc(self, name, layout):
        self.widget = QWidget()
        self.widget.setLayout(layout)
        self.tabWidget.addTab(self.widget, name)
        self.tabWidget.setCurrentWidget(self.widget)

    def addToTab(self, tabName, type, content):
        tabLayout = QHBoxLayout()
        if type == 'list':
            textEditWidget = QTextEdit()
            textEditWidget.insertPlainText(content)
            textEditWidget.setReadOnly(True)
            tabLayout.addWidget(textEditWidget)

        elif type == 'tree':
            #yarascan
            yarascanClass= yarascanTreeView.yarascanTreeView(content)
            tabLayout.addWidget(yarascanClass.treeWidget)

        elif type == 'table':
            # number of columns depends on number of keys in dict
            num_of_columns = len(content)
            num_of_rows = len(content[content.keys()[0]])
            #tableWidget = QTableWidget(num_of_rows, num_of_columns)

            tableWidget = TableWidget(self, content, tabName, num_of_rows, num_of_columns)
            tabLayout.addWidget(tableWidget)

        self.addTabFnc(tabName, tabLayout)
        # self.dirty = True

    def closeTab(self, currentIndex):
        currentQWidget = self.tabWidget.widget(currentIndex)
        currentQWidget.deleteLater()
        self.tabWidget.removeTab(currentIndex)

    def showImageTab(self):

        items = ['Use Imageinfo', 'VistaSP0x64', 'VistaSP0x86', 'VistaSP1x64', 'VistaSP2x64', \
                 'VistaSP2x86', 'Win2003SP0x86', 'Win2003SP1x64', 'Win2003SP1x86', 'Win2003SP2x64', \
                 'Win2003SP2x86', 'Win2008R2SP0x64', 'Win2008R2SP1x64', 'Win2008SP1x64', 'Win2008SP1x86', \
                 'Win2008SP2x64', 'Win7SP0x64', 'Win7SP0x86', 'Win7SP1x64', 'Win7SP1x86', 'WinXPSP1x64', \
                 'WinXPSP2x64', 'WinXPSP2x86', 'WinXPSP3x86']

        fileNameLabel = QLabel("Image: ")
        profileLabel = QLabel("Profile: ")
        fileName = QLabel(self.filename)
        self.profileSelector = QComboBox()
        self.profileSelector.addItems(items)

        #
        index = items.index(self.profile)

        self.profileSelector.setCurrentIndex(index)
        horizontalLayout = QHBoxLayout()
        grid = QGridLayout()
        grid.addWidget(fileNameLabel, 1, 0)
        grid.addWidget(fileName, 1, 1)
        grid.addWidget(profileLabel, 2, 0)
        grid.addWidget(self.profileSelector, 2, 1)
        spacerItem = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)
        grid.addItem(spacerItem)
        horizontalLayout.addItem(grid)
        horizontalLayout.addStretch()

        self.connect(self.profileSelector, SIGNAL("currentIndexChanged(QString)"), self.storeProfile)
        self.addTabFnc("Image", horizontalLayout)
        self.dirty = True

    def checkForTable(self, tableName):
        # This function will determine if we already got a module output in db
        # input: tableName
        # output: true/false
        db = dbmodule.sqlitequery(tableName, self.output_path)
        presence = db.checkForTable()
        return presence

    def showAboutInfo(self):
        QMessageBox.about(self, "About yavol",
                          "yavol version %s\n\nCopyright(c) 2015 by %s\n" % (__version__, __author__))

    def showWarningInfo(self, warning_title, warning_text):
        QMessageBox.warning(self, warning_title, warning_text, QMessageBox.Ok)

    def displayInLog(self, content):
        self.listWidget.addItem(content)

    #def hashfile(self, afile, hasher, blocksize=65536):

    #    for block in iter(lambda: afile.read(blocksize), ''):
    #        hasher.update(block)
    #    return hasher.hexdigest()

    def doNothing(self):
        self.dirty = True
        self.displayInLog("Nothing was done, really")


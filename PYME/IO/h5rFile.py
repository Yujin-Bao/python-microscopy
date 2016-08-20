import tables
import logging
import threading
import time
from PYME.IO import MetaDataHandler
import numpy as np

#global lock across all instances of the H5RFile class as we can have problems across files
tablesLock = threading.Lock()

file_cache = {}


def openH5R(filename, mode='r'):
    key = (filename, mode)
    if key in file_cache and file_cache[key].is_alive:
        return file_cache[key]
    else:
        file_cache[key] = H5RFile(filename, mode)



KEEP_ALIVE_TIMEOUT = 20 #keep the file open for 20s after the last time it was used

class H5RFile(object):
    def __init__(self, filename, mode='r'):
        self.filename = filename
        self.mode = mode

        self._h5file = tables.openFile(filename, mode)

        #metadata and events are created on demand
        self._mdh = None
        self._events = None

        # lock for adding things to our queues. This is local to the file and synchronises between the calling thread
        # and our local thread
        self.appendQueueLock = threading.Lock()
        self.appendQueues = {}

        self.keepAliveTimeout = time.time() + KEEP_ALIVE_TIMEOUT
        self.useCount = 0
        self.is_alive = True

        self._pollThread = threading.Thread(target=self._pollQueues())
        self._pollThread.start()

    def __enter__(self):
        with self.appendQueueLock:
            self.useCount += 1

    def __exit__(self, *args):
        with self.appendQueueLock:
            self.keepAliveTimeout = time.time() + KEEP_ALIVE_TIMEOUT
            self.useCount -= 1


    @property
    def mdh(self):
        if self._mdh is None:
            try:
                self._mdh = MetaDataHandler.HDFMDHandler(self._h5file)
                if self.mode == 'r':
                    self._mdh = MetaDataHandler.NestedClassMDHandler(self._mdh)
            except IOError:
                # our file was opened in read mode and didn't have any metadata to start with
                self._mdh = MetaDataHandler.NestedClassMDHandler()

        return self._mdh

    def updateMetadata(self, mdh):
        """Update the metadata, acquiring the necessary locks"""
        with tablesLock:
            self.mdh.update(mdh)

    @property
    def events(self):
        if self._events is None:
            try:
                self._events = self._h5file.root.Events
            except AttributeError:
                if not self.mode == 'r':
                    class SpoolEvent(tables.IsDescription):
                        EventName = tables.StringCol(32)
                        Time = tables.Time64Col()
                        EventDescr = tables.StringCol(256)

                    with tablesLock:
                        self._events = self._h5file.createTable(self._h5file.root, 'Events', SpoolEvent,
                                                                        filters=tables.Filters(complevel=5, shuffle=True))
                else:
                    # our file was opened in read mode and didn't have any events to start with
                    self._events = []

        return self._events

    def _appendToTable(self, tablename, data):
        with tablesLock:
            try:
                table = getattr(self._h5file.root, tablename)
                table.append(data)
            except AttributeError:
                # we don't have a table with that name - create one
                self._h5file.createTable(self._h5file.root, tablename, data,
                                               filters=tables.Filters(complevel=5, shuffle=True),
                                               expectedrows=500000)

    def appendToTable(self, tablename, data):
        with self.appendQueueLock:
            if not tablename in self.appendQueues.keys():
                self.appendQueues[tablename] = []
            self.appendQueues[tablename].append(data)

    def _pollQueues(self):
        queuesWithData = False

        try:
            while self.useCount > 0 or queuesWithData or time.time() < self.keepAliveTimeout:
                with self.appendQueueLock:
                    #find queues with stuff to save
                    tablenames = [k for k, v in self.appendQueues.items() if len(v) > 0]

                queuesWithData = len(tablenames) > 0

                #iterate over the queues
                for tablename in tablenames:
                    with self.appendQueueLock:
                        entries = self.appendQueues[tablename]
                        self.appendQueues[tablename] = []

                    #save the data - note that we can release the lock here, as we are the only ones calling this function.
                    self._appendToTable(tablename, np.hstack(entries))

                time.sleep(0.002)

        finally:
            #remove ourselves from the cache
            file_cache.pop((self.filename, self.mode))

            self.is_alive = False
            #finally, close the file
            self._h5file.close()



    def fileFitResult(self, fitResult):
        """
        Legacy handling for fitResult objects as returned by remFitBuf

        Parameters
        ----------
        fitResult

        Returns
        -------

        """
        if len(fitResult.results) > 0:
            self.appendToTable('FitResults', fitResult.results)

        if len(fitResult.driftResults) > 0:
            self.appendToTable('DriftResults', fitResult.driftResults)
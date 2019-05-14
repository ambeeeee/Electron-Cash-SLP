import copy
import datetime
import time
from functools import partial
import json, io
import threading
import sys
from pathlib import Path
from os.path import basename, splitext

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *

from electroncash.address import Address, PublicKey, Base58Error
from electroncash.bitcoin import base_encode, TYPE_ADDRESS, TYPE_SCRIPT
from electroncash.i18n import _
from electroncash.plugins import run_hook

from .util import *

from electroncash.util import bfh, format_satoshis_nofloat, format_satoshis_plain_nofloat, NotEnoughFunds, ExcessiveFee, InvalidPassword
from electroncash.transaction import Transaction

from electroncash import bitcoinfiles

from .transaction_dialog import show_transaction

from electroncash.bitcoinfiles import *

dialogs = []  # Otherwise python randomly garbage collects the dialogs...


class BitcoinFilesUploadDialog(QDialog, MessageBoxMixin):

    def __init__(self, parent, file_receiver=None, show_on_create=False, screen_name="Upload Token Document"):
        # We want to be a top-level window
        QDialog.__init__(self, parent)

        # check parent window type
        self.parent = parent
        from .slp_create_token_genesis_dialog import SlpCreateTokenGenesisDialog
        from .main_window import ElectrumWindow
        if isinstance(parent, SlpCreateTokenGenesisDialog):
            self.main_window = parent.main_window
            self.wallet = parent.main_window.wallet
            self.network = parent.main_window.network
        elif isinstance(parent, ElectrumWindow):
            self.main_window = parent
            self.wallet = parent.wallet
            self.network = parent.network
        else:
            raise Exception("Parent must be of type ElectrumWindow or SlpCreateTokenGenesisDialog")

        self.file_receiver = file_receiver
        self.metadata = None
        self.local_file_path = None
        self.is_dirty = False
        self.password = None

        self.setWindowTitle(_(screen_name))

        vbox = QVBoxLayout()
        self.setLayout(vbox)

        #vbox.addWidget(QLabel("Upload and download documents using the Bitcoin Files Protocol (<a href=https://bitcoinfiles.com>bitcoinfiles.com</a>)"))

        grid = QGridLayout()
        #grid.setColumnStretch(1, 1)
        vbox.addLayout(grid)
        row = 0

        # Local file path
        # grid.addWidget(QLabel(_("Local 'token.json' path:")), row, 0)
        # hbox = QHBoxLayout()
        # self.path = QLineEdit("")
        # self.path.setReadOnly(True)
        # self.path.setFixedWidth(450)
        # hbox.addWidget(self.path)

        # self.select_file_button = b = QPushButton(_("Browse..."))
        # b.setAutoDefault(False)
        # b.setDefault(False)
        # b.clicked.connect(self.select_local_document)
        # hbox.addWidget(b)
        # grid.addLayout(hbox, row, 1)
        # row += 1

        # Document Filename
        grid.addWidget(QLabel(_('Filename:')), row, 0)
        self.filename_e = QLineEdit("token.json")
        self.filename_e.setDisabled(True)
        self.filename_e.setReadOnly(True)
        self.filename_e.setFixedWidth(570)
        self.filename_e.textChanged.connect(self.make_dirty)
        grid.addWidget(self.filename_e, row, 1)
        row += 1

        # Contact Email
        grid.addWidget(QLabel(_('Contact Email:')), row, 0)
        self.email_e = QLineEdit("")
        self.email_e.setReadOnly(False)
        self.email_e.setFixedWidth(570)
        self.email_e.textChanged.connect(self.make_dirty)
        grid.addWidget(self.email_e, row, 1)
        row += 1

        # Contact URL:
        grid.addWidget(QLabel(_('Contact URL:')), row, 0)
        self.url_e = QLineEdit("")
        self.url_e.setReadOnly(False)
        self.url_e.setFixedWidth(570)
        self.url_e.textChanged.connect(self.make_dirty)
        grid.addWidget(self.url_e, row, 1)
        row += 1

        # Token.json builder
        grid.addWidget(QLabel(_('Token.json Icon SVG:')), row, 0)
        hbox = QHBoxLayout()
        self.svg_path_e = QLineEdit("")
        self.svg_path_e.setReadOnly(True)
        self.svg_path_e.setFixedWidth(450)
        hbox.addWidget(self.svg_path_e)

        b = QPushButton(_("Browse..."))
        b.clicked.connect(self.select_svg_file)
        hbox.addWidget(b)
        
        grid.addLayout(hbox, row, 1)
        row += 1

        # Previous file hash
        grid.addWidget(QLabel(_('Previous file sha256 (manual entry):')), row, 0)
        self.prev_hash = QLineEdit("")
        self.prev_hash.setReadOnly(False)
        self.prev_hash.setFixedWidth(570)
        self.prev_hash.setInputMask("HHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHH")
        self.prev_hash.textChanged.connect(self.make_dirty)
        grid.addWidget(self.prev_hash, row, 1)
        row += 1

        # Originating address checkbox
        self.org_addr_cb = cb = QCheckBox(_('Upload file using a specific wallet address'))
        self.org_addr_cb.setChecked(False)
        grid.addWidget(self.org_addr_cb, row, 1)
        cb.clicked.connect(self.toggle_org_addr)
        row += 1

        # Specific file origination address
        self.org_add_label = QLabel(_('Originating Address for Upload:'))
        self.org_add_label.setHidden(True)
        grid.addWidget(self.org_add_label, row, 0)
        self.file_org_addr_e = QLineEdit("")
        self.file_org_addr_e.setHidden(True)
        self.file_org_addr_e.setReadOnly(False)
        self.file_org_addr_e.setFixedWidth(570)
        self.file_org_addr_e.textChanged.connect(self.make_dirty)
        grid.addWidget(self.file_org_addr_e, row, 1)
        row += 1

        # File Receiver Checkbox
        self.receiver_addr_cb = cb = QCheckBox(_('Send file to a BCH address'))
        self.receiver_addr_cb.setChecked(False)
        grid.addWidget(self.receiver_addr_cb, row, 1)
        cb.clicked.connect(self.toggle_receiver_addr)
        row += 1

        # Specific file receiver
        self.file_receiver_label = QLabel(_('Receiver Address:'))
        self.file_receiver_label.setHidden(True)
        grid.addWidget(self.file_receiver_label, row, 0)
        self.file_receiver_e = QLineEdit("")
        self.file_receiver_e.setHidden(True)
        self.file_receiver_e.setReadOnly(False)
        self.file_receiver_e.setFixedWidth(570)
        self.file_receiver_e.textChanged.connect(self.make_dirty)
        grid.addWidget(self.file_receiver_e, row, 1)
        row += 1

        # Estimated Fees
        grid.addWidget(QLabel(_('Upload Cost (satoshis):')), row, 0)
        self.upload_cost_label = QLabel("")
        grid.addWidget(self.upload_cost_label, row, 1)
        row += 1

        # File hash
        grid.addWidget(QLabel(_('Token Document sha256:')), row, 0)
        self.hash = QLineEdit("")
        self.hash.setReadOnly(True)
        self.hash.setDisabled(True)
        self.hash.setFixedWidth(570)
        self.hash.setInputMask("HHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHH")
        grid.addWidget(self.hash, row, 1)
        row += 1

        # Token Document URI
        grid.addWidget(QLabel(_('Token Document URI:')), row, 0)
        self.bitcoinfileAddr_label = QLineEdit("")
        self.bitcoinfileAddr_label.setReadOnly(True)
        self.bitcoinfileAddr_label.setDisabled(True)
        self.bitcoinfileAddr_label.setFixedWidth(570)
        grid.addWidget(self.bitcoinfileAddr_label, row, 1)

        self.progress_label = QLabel("")
        vbox.addWidget(self.progress_label)

        self.progress = QProgressBar(self)
        self.progress.setHidden(True)
        self.progress.setGeometry(200, 80, 250, 20)
        vbox.addWidget(self.progress)

        hbox = QHBoxLayout()
        vbox.addLayout(hbox)

        self.cancel_button = b = QPushButton(_("Cancel"))
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        b.clicked.connect(self.close)
        b.setDefault(False)
        hbox.addWidget(self.cancel_button)

        hbox.addStretch(1)

        self.sign_button = b = QPushButton(_("Sign"))
        self.sign_button.setAutoDefault(True)
        self.sign_button.setDefault(True)
        b.clicked.connect(self.sign_bfp_txns)
        b.setDefault(False)
        hbox.addWidget(self.sign_button)

        self.upload_button = b = QPushButton(_("Upload"))
        self.upload_button.setAutoDefault(False)
        self.upload_button.setDefault(False)
        self.upload_button.setDisabled(True)
        b.clicked.connect(self.upload)
        b.setDefault(False)
        hbox.addWidget(self.upload_button)

        hbox = QHBoxLayout()
        vbox.addLayout(hbox)

        warnpm = QIcon(":icons/warning.png").pixmap(20,20)

        l = QLabel(); l.setPixmap(warnpm)
        hbox.addWidget(l)
        hbox.addWidget(QLabel(_('            WARNING: The selected file will be uploaded to the blockchain and be permanently part of the public record.')))
        l = QLabel(); l.setPixmap(warnpm)
        hbox.addStretch(1)
        hbox.addWidget(l)

        # check if self.password is needed for wallet
        if parent.wallet.has_password():
            from .password_dialog import PasswordDialog
            parent = parent
            d = PasswordDialog(parent, None)
            self.password = d.run()

        if show_on_create:
            self.setModal(True)
            self.show()

    def toggle_org_addr(self):
        self.file_org_addr_e.setVisible(not self.file_org_addr_e.isVisible())
        self.org_add_label.setVisible(not self.org_add_label.isVisible())
        if not self.file_org_addr_e.isVisible():
            self.file_org_addr_e.setText('')

    def toggle_receiver_addr(self):
        self.file_receiver_e.setVisible(not self.file_receiver_e.isVisible())
        self.file_receiver_label.setVisible(not self.file_receiver_label.isVisible())
        if not self.file_receiver_e.isVisible():
            self.file_receiver_e.setText('')

    def make_dirty(self):
        self.is_dirty = True
        self.upload_button.setDisabled(True)
        self.progress.setValue(0)
        self.tx_batch = []
        self.tx_batch_signed_count = 0
        self.chunks_processed = 0
        self.chunks_total = 0
        self.final_metadata_txn_created = False
        self.upload_cost_label.setText('')
        self.sign_button.setEnabled(True)
        self.sign_button.setDefault(True)

    def sign_bfp_txns(self):
        if self.local_file_path == "" or self.local_file_path == None:
            json = self.rebuild_token_json()
            self._sign_bfp_txns(json)
            return
        with open(self.local_file_path, 'rb') as f:
            self._sign_bfp_txns(f)

    def _sign_bfp_txns(self, file_data):
            #with open(self.local_file_path, "rb") as f:

        # set all file Metadata to None for now... UI needs updated for this
        self.metadata = { 'filename': None, 'fileext': None, 'filesize': None, 'file_sha256': None, 'prev_file_sha256': None, 'uri': None }
        self.metadata['prev_file_sha256'] = self.prev_hash.text()

        if self.prev_hash.text() != '':
            if len(self.prev_hash.text()) != 64:
                self.show_message(_("Previous document hash must be a 32 byte hexidecimal string or left empty."))
                return

        if self.file_org_addr_e.text() != '':
            try: 
                Address.from_string(self.file_org_addr_e.text())
            except Base58Error:
                self.show_message(_("Originating address checksum fails."))
                return

        if self.file_receiver_e.text() != '':
            try: 
                #addr = Address.from_string(self.file_receiver_e.text())
                Address.from_string(self.file_receiver_e.text())
            except Base58Error:
                self.show_message(_("Receiver address checksum fails."))
                return
            
            self.file_receiver = Address.from_string(self.file_receiver_e.text())
        else:
            self.file_receiver = None
    
        if self.local_file_path == '' or self.local_file_path == None:
            filename = 'token.json'
            fileext = 'json'
        else:
            s = basename(self.local_file_path).split(os.extsep, 1)
            filename = basename(self.local_file_path)
            fileext = s[len(s)-1]

        #self.select_file_button.setDefault(False)
        #with open(self.local_file_path, "rb") as f:
        # clear fields before re-populating
        self.hash.setText('')
        #self.path.setText('')
        self.upload_cost_label.setText('')
        self.bitcoinfileAddr_label.setText('')

        bytes = file_data.read()
        if len(bytes) > 5261:
            self.show_error("Files cannot be larger than 5.261kB in size.")
            return
        import hashlib
        readable_hash = hashlib.sha256(bytes).hexdigest()
        self.hash.setText(readable_hash)
        self.metadata['file_sha256'] = readable_hash

        #self.path.setText(self.local_file_path)
        self.metadata['filesize'] = len(bytes)
        self.metadata['filename'] = filename
        self.metadata['fileext'] = fileext

        cost = calculateUploadCost(len(bytes), self.metadata)
        self.upload_cost_label.setText(str(cost))
        if(self.org_addr_cb.isChecked and self.file_org_addr_e.text() != ''):
            addr = Address.from_string(self.file_org_addr_e.text())
        else:
            addr = self.parent.wallet.get_unused_address()

        # # IMPORTANT: set wallet.send_slpTokenId to None to guard tokens during this transaction
        self.main_window.token_type_combo.setCurrentIndex(0)
        assert self.main_window.slp_token_id == None

        try:
            self.tx_batch.append(getFundingTxn(self.parent.wallet, addr, cost, self.parent.config))
            self.progress_label.setText('')
        except NotEnoughFunds:
            self.show_message("Insufficient funds.\n\nYou must have a CONFIRMED balance of at least: " + str(cost) + " satoshis.")
            self.progress_label.setText('')
            self.local_file_path = None
            self.make_dirty()
            return

        # Rewind and put file into chunks
        file_data.seek(0, 0)
        chunks = []
        while True:
            b = file_data.read(220)
            if b == b'': break
            try:
                chunks.append(b)
                self.chunks_total += 1
            except ValueError:
                break

        min_len = 223 - len(make_bitcoinfile_metadata_opreturn(1, 0, None, self.metadata['filename'], self.metadata['fileext'], self.metadata['filesize'], self.metadata['file_sha256'], self.metadata['prev_file_sha256'], self.metadata['uri'])[1].to_script())
        
        # determine if the metadata txn data chunk will be empty for progress bar accuracy
        if len(bytes) < 220:
            chunk_count_adder = 1 if len(bytes) > min_len else 0
        else:
            chunk_count_adder = 1 if min_len - (len(bytes) % 220) < 0 else 0

        self.progress.setMaximum(len(chunks) + chunk_count_adder + 1)
        self.progress.setMinimum(0)
        self.progress.setVisible(True)
        self.progress_label.setText("Signing 1 of " + str(len(chunks) + chunk_count_adder + 1) + " transactions")
        
        # callback to recursive sign next txn or finish
        def sign_done(success):
            if success:
                self.tx_batch_signed_count += 1
                self.progress.setValue(self.tx_batch_signed_count)
                self.activateWindow()
                self.raise_()
                self.progress_label.setText("Signing " + str(self.tx_batch_signed_count + 1) + " of " + str(len(chunks) + chunk_count_adder + 1) + " transactions")
                if self.chunks_processed <= self.chunks_total and not self.final_metadata_txn_created:
                    try:
                        chunk_bytes = chunks[self.chunks_processed]
                    except IndexError:
                        chunk_bytes = None
                    try:
                        txn, self.final_metadata_txn_created = getUploadTxn(self.parent.wallet, self.tx_batch[self.chunks_processed], self.chunks_processed, self.chunks_total, chunk_bytes, self.parent.config, self.metadata, self.file_receiver)
                        self.tx_batch.append(txn)
                    except NotEnoughFunds as e:
                        self.show_message("Insufficient funds for file chunk #" + str(self.chunks_processed + 1))
                        return
                    self.chunks_processed += 1

                if self.tx_batch_signed_count < len(self.tx_batch):
                    self.sign_tx_with_password(self.tx_batch[self.tx_batch_signed_count], sign_done, self.password)
                else:
                    uri = "bitcoinfile:" + self.tx_batch[len(self.tx_batch)-1].txid()
                    self.bitcoinfileAddr_label.setText(uri)
                    self.progress_label.setText("Signing complete. Ready to upload.")
                    self.progress.setHidden(True)
                    self.is_dirty = False
                    self.progress.setValue(0)
                    self.sign_button.setDisabled(True)
                    self.upload_button.setEnabled(True)
                    self.upload_button.setDefault(True)
                    self.activateWindow()
                    self.raise_()
        self.sign_tx_with_password(self.tx_batch[0], sign_done, self.password)

    def sign_tx_with_password(self, tx, callback, password):
        '''Sign the transaction in a separate thread.  When done, calls
        the callback with a success code of True or False.
        '''
        # call hook to see if plugin needs gui interaction
        run_hook('sign_tx', self, tx)

        def on_signed(result):
            callback(True)
        def on_failed(exc_info):
            self.on_error(exc_info)
            callback(False)

        if self.main_window.tx_external_keypairs:
            task = partial(Transaction.sign, tx, self.main_window.tx_external_keypairs)
        else:
            task = partial(self.wallet.sign_transaction, tx, password)
        WaitingDialog(self, _('Signing transaction...'), task, on_signed, on_failed)


    def select_svg_file(self):
        if self.wallet.has_password():
            if self.password == None:
                x = self.show_message("Incorrect password.")
                self.close()
                return
            try:
                self.wallet.check_password(self.password)
            except InvalidPassword:
                x = self.show_message("Incorrect password.")
                self.close()
                return

        self.progress.setValue(0)
        self.tx_batch = []
        self.tx_batch_signed_count = 0
        self.chunks_processed = 0
        self.chunks_total = 0
        self.final_metadata_txn_created = False
        options = QFileDialog.Options()
        #options |= QFileDialog.DontUseNativeDialog
        home = str(Path.home())
        self.svg_file_path, _ = QFileDialog.getOpenFileName(self, "Select SVG for token.json", home, "svg(*.svg)", options=options)
        self.svg_path_e.setText(self.svg_file_path)
        self.make_dirty()
        # json = self.rebuild_token_json()
        # self.sign_bfp_txns(json)

    def rebuild_token_json(self):
        data = {}
        data['v'] = 0
        data['update_auth'] = []
        data['contact'] = {}
        data['contact']['email'] = self.email_e.text()
        data['contact']['url'] = self.url_e.text()
        data['contact']['phone'] = ""
        with open(self.svg_file_path, "rb") as f:
                data['icon_svg'] = f.read().decode('utf8')
        return io.BytesIO(json.dumps(data).encode('utf8'))

    def select_local_document(self):
        if self.wallet.has_password():
            if self.password == None:
                x = self.show_message("Incorrect password.")
                self.close()
                return
            try:
                self.wallet.check_password(self.password)
            except InvalidPassword:
                x = self.show_message("Incorrect password.")
                self.close()
                return

        self.progress.setValue(0)
        self.tx_batch = []
        self.tx_batch_signed_count = 0
        self.chunks_processed = 0
        self.chunks_total = 0
        self.final_metadata_txn_created = False
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        home = str(Path.home())
        self.local_file_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload", home, "All Files (*)", options=options)
        self.make_dirty()
        
    def upload(self):
        if not self.is_dirty:
            self.progress_label.setText("Broadcasting 1 of " + str(len(self.tx_batch)) + " transactions")
            self.progress.setVisible(True)
            self.progress.setMinimum(0)
            self.progress.setMaximum(len(self.tx_batch))
            broadcast_count = 0
            # Broadcast all transaction to the nexwork
            for tx in self.tx_batch:
                tx_desc = None
                status, msg = self.network.broadcast_transaction(tx)
                # print(status)
                # print(msg)
                if status == False:
                    self.show_error(msg)
                    self.show_error("Upload failed. Try again.")
                    return

                if tx_desc is not None and tx.is_complete():
                    self.wallet.set_label(tx.txid(), "File Upload: " + str(broadcast_count) + " of " + str(len(self.tx_batch)))

                broadcast_count += 1
                time.sleep(0.1)
                self.progress_label.setText("Broadcasting " + str(broadcast_count) + " of " + str(len(self.tx_batch)) + " transactions")
                self.progress.setValue(broadcast_count)
                QApplication.processEvents()

            self.progress_label.setText("Broadcasting complete.")
            self.progress.setHidden(True)
            try:
                self.parent.token_dochash_e.setText(self.hash.text())
                self.parent.token_url_e.setText(self.bitcoinfileAddr_label.text())
            except AttributeError:
                pass

            self.show_message("File upload complete.")
            self.close()

    def closeEvent(self, event):
        event.accept()
        self.parent.raise_()
        self.parent.activateWindow()
        try:
            dialogs.remove(self)
        except ValueError:
             pass
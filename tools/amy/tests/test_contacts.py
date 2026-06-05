import platform
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def qapp():
    """Ensure a QApplication exists for Qt widget tests in this module."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestContactAutocomplete:
    def test_set_contacts_populates_model(self, qapp):
        from amy.gui_viewer import ContactAutocomplete
        widget = ContactAutocomplete()
        widget.set_contacts([
            {"name": "Alice", "email": "alice@example.com"},
            {"name": "Bob", "email": "bob@example.com"},
        ])
        model = widget._completer.model()
        assert model.rowCount() == 2

    def test_get_text_returns_trimmed(self, qapp):
        from amy.gui_viewer import ContactAutocomplete
        widget = ContactAutocomplete()
        widget.setText("  test@example.com  ")
        assert widget.get_text() == "test@example.com"

    def test_free_text_accepted(self, qapp):
        from amy.gui_viewer import ContactAutocomplete
        widget = ContactAutocomplete()
        widget.set_contacts([{"name": "Alice", "email": "alice@example.com"}])
        widget.setText("someone@external.com")
        assert widget.get_text() == "someone@external.com"

    def test_empty_contacts_does_not_crash(self, qapp):
        from amy.gui_viewer import ContactAutocomplete
        widget = ContactAutocomplete()
        widget.set_contacts([])
        widget.setText("anything")
        assert widget.get_text() == "anything"

    def test_can_set_and_clear_text(self, qapp):
        from amy.gui_viewer import ContactAutocomplete
        widget = ContactAutocomplete()
        widget.setText("hello")
        assert widget.get_text() == "hello"
        widget.setText("")
        assert widget.get_text() == ""


class TestCcRecipientRow:
    def test_initial_state(self, qapp):
        from amy.gui_viewer import CcRecipientRow
        row = CcRecipientRow()
        assert row.get_text() == ""

    def test_set_and_get_text(self, qapp):
        from amy.gui_viewer import CcRecipientRow
        row = CcRecipientRow()
        row.set_text("  Alice <alice@x.com>  ")
        assert row.get_text() == "Alice <alice@x.com>"

    def test_set_contacts_propagates(self, qapp):
        from amy.gui_viewer import CcRecipientRow
        row = CcRecipientRow()
        row.set_contacts([{"name": "Test", "email": "t@x.com"}])
        model = row.le_cc._completer.model()
        assert model.rowCount() == 1

    def test_remove_signal_emitted(self, qapp):
        from amy.gui_viewer import CcRecipientRow
        row = CcRecipientRow()
        removed = []
        row.removed.connect(lambda w: removed.append(w))
        row.btn_remove.click()
        assert len(removed) == 1
        assert removed[0] is row

    def test_setEnabled_propagates(self, qapp):
        from amy.gui_viewer import CcRecipientRow
        row = CcRecipientRow()
        row.setEnabled(False)
        assert not row.le_cc.isEnabled()
        assert not row.btn_clear.isEnabled()
        assert not row.btn_remove.isEnabled()
        row.setEnabled(True)
        assert row.le_cc.isEnabled()
        assert row.btn_clear.isEnabled()
        assert row.btn_remove.isEnabled()

    def test_clear_button_clears_text(self, qapp):
        from amy.gui_viewer import CcRecipientRow
        row = CcRecipientRow()
        row.set_text("alice@example.com")
        row.btn_clear.click()
        assert row.get_text() == ""


class TestCcSection:
    def test_empty_section_has_no_rows(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        assert section.get_cc_string() == ""

    def test_set_from_cc_string_parses_recipients(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_from_cc_string("Alice <a@x.com>; Bob <b@x.com>")
        rows = section.get_rows()
        assert len(rows) == 2
        assert rows[0].get_text() == "Alice <a@x.com>"
        assert rows[1].get_text() == "Bob <b@x.com>"

    def test_get_cc_string_reconstructs(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_from_cc_string("Alice <a@x.com>; Bob <b@x.com>")
        assert section.get_cc_string() == "Alice <a@x.com>; Bob <b@x.com>"

    def test_skips_empty_entries_in_reconstruction(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_from_cc_string("Alice <a@x.com>; Bob <b@x.com>")
        section.get_rows()[1].le_cc.setText("")
        assert section.get_cc_string() == "Alice <a@x.com>"

    def test_empty_string_creates_no_rows(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_from_cc_string("")
        assert section.get_rows() == []

    def test_whitespace_only_creates_no_rows(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_from_cc_string("   ")
        assert section.get_rows() == []

    def test_add_row_via_button(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.add_row(initial_text="new@x.com")
        assert len(section.get_rows()) == 1
        assert section.get_rows()[0].get_text() == "new@x.com"

    def test_remove_row(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_from_cc_string("A <a@x.com>; B <b@x.com>")
        row = section.get_rows()[0]
        row.btn_remove.click()
        assert len(section.get_rows()) == 1
        assert section.get_rows()[0].get_text() == "B <b@x.com>"

    def test_clear_rows(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_from_cc_string("A <a@x.com>; B <b@x.com>; C <c@x.com>")
        section.clear_rows()
        assert section.get_rows() == []
        assert section.get_cc_string() == ""

    def test_set_contacts_propagates_to_existing_rows(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.add_row(initial_text="alice@x.com")
        section.set_contacts([{"name": "Alice", "email": "alice@x.com"}])
        for row in section.get_rows():
            model = row.le_cc._completer.model()
            assert model.rowCount() == 1

    def test_set_contacts_stored_for_new_rows(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_contacts([{"name": "Test", "email": "t@x.com"}])
        section.add_row()
        model = section.get_rows()[0].le_cc._completer.model()
        assert model.rowCount() == 1

    def test_setEnabled_propagates(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.add_row(initial_text="a@x.com")
        section.setEnabled(False)
        for row in section.get_rows():
            assert not row.le_cc.isEnabled()
        assert not section.btn_add.isEnabled()

        section.setEnabled(True)
        for row in section.get_rows():
            assert row.le_cc.isEnabled()
        assert section.btn_add.isEnabled()

    def test_multiple_rows_independent(self, qapp):
        from amy.gui_viewer import CcSection
        section = CcSection()
        section.set_from_cc_string("A <a@x.com>; B <b@x.com>")
        section.get_rows()[0].le_cc.setText("Changed <c@x.com>")
        assert section.get_cc_string() == "Changed <c@x.com>; B <b@x.com>"


class TestFetchOutlookContacts:
    """Tests for fetch_outlook_contacts — mocked COM, no real Outlook needed."""

    @pytest.fixture
    def mock_outlook(self):
        """Create a mock Outlook COM namespace with GAL and Contacts folder."""
        def make_gal_entry(name, email):
            entry = MagicMock()
            entry.Name = name
            entry.Address = email
            exch = MagicMock()
            exch.PrimarySmtpAddress = email
            entry.GetExchangeUser.return_value = exch
            return entry

        gal_entries = [
            make_gal_entry("Alice Smith", "alice@example.com"),
            make_gal_entry("Bob Jones", "bob@example.com"),
            make_gal_entry("Carol Wu", "carol@example.com"),
        ]

        gal = MagicMock()
        gal.AddressEntries.Count = len(gal_entries)
        gal.AddressEntries.Item.side_effect = lambda i: gal_entries[i - 1]

        def make_contact_item(full_name, email1, email2="", email3=""):
            item = MagicMock()
            item.FullName = full_name
            item.Email1Address = email1
            item.Email2Address = email2
            item.Email3Address = email3
            return item

        contacts_items = [
            make_contact_item("Dave Brown", "dave@example.com"),
            make_contact_item("", "", "eve@example.com"),
        ]

        contacts_folder = MagicMock()
        contacts_folder.Items = contacts_items

        namespace = MagicMock()
        namespace.GetGlobalAddressList.return_value = gal
        namespace.GetDefaultFolder.return_value = contacts_folder

        dispatch = MagicMock()
        dispatch.GetNamespace.return_value = namespace

        return dispatch

    @pytest.fixture
    def com_mocks(self, mock_outlook):
        """Install mocked win32com into sys.modules with proper package hierarchy."""
        mock_client = MagicMock()
        mock_client.Dispatch.return_value = mock_outlook

        mock_win32com = MagicMock()
        mock_win32com.client = mock_client
        return {
            "win32com": mock_win32com,
            "win32com.client": mock_client,
        }

    def test_returns_list_of_dicts(self, com_mocks):
        with patch.dict(sys.modules, com_mocks):
            from shared_tools.outlook_tool import fetch_outlook_contacts
            contacts = fetch_outlook_contacts()
            assert isinstance(contacts, list)
            assert len(contacts) >= 1
            for c in contacts:
                assert "name" in c
                assert "email" in c

    def test_deduplicates_by_email(self, com_mocks, mock_outlook):
        def make_gal_entry(name, email):
            entry = MagicMock()
            entry.Name = name
            entry.Address = email
            exch = MagicMock()
            exch.PrimarySmtpAddress = email
            entry.GetExchangeUser.return_value = exch
            return entry

        gal = mock_outlook.GetNamespace.return_value.GetGlobalAddressList.return_value
        gal.AddressEntries.Item.side_effect = None
        gal.AddressEntries.Count = 3
        entries = [
            make_gal_entry("Alice Smith", "alice@example.com"),
            make_gal_entry("Alice Dup", "alice@example.com"),
            make_gal_entry("Bob Jones", "bob@example.com"),
        ]
        gal.AddressEntries.Item.side_effect = lambda i: entries[i - 1]

        with patch.dict(sys.modules, com_mocks):
            from shared_tools.outlook_tool import fetch_outlook_contacts
            contacts = fetch_outlook_contacts()
            emails = [c["email"] for c in contacts]
            assert emails.count("alice@example.com") == 1

    def test_results_sorted_by_name(self, com_mocks):
        with patch.dict(sys.modules, com_mocks):
            from shared_tools.outlook_tool import fetch_outlook_contacts
            contacts = fetch_outlook_contacts()
            names = [c["name"].lower() for c in contacts]
            assert names == sorted(names)

    def test_filters_x500_addresses(self, com_mocks, mock_outlook):
        entry = MagicMock()
        entry.Name = "Bad Entry"
        entry.Address = "/O=EXCHANGE/OU=ADMIN/cn=Recipients/cn=bad"
        entry.GetExchangeUser.return_value = None

        # Also clear Contacts folder so we only test the GAL X.500 filtering
        namespace = mock_outlook.GetNamespace.return_value
        namespace.GetDefaultFolder.return_value.Items = []

        gal = namespace.GetGlobalAddressList.return_value
        gal.AddressEntries.Count = 1
        gal.AddressEntries.Item.side_effect = lambda i: entry

        with patch.dict(sys.modules, com_mocks):
            from shared_tools.outlook_tool import fetch_outlook_contacts
            contacts = fetch_outlook_contacts()
            assert len(contacts) == 0

    def test_returns_empty_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        from shared_tools.outlook_tool import fetch_outlook_contacts
        contacts = fetch_outlook_contacts()
        assert contacts == []

    def test_contacts_folder_fallback(self, com_mocks, mock_outlook):
        namespace = mock_outlook.GetNamespace.return_value
        namespace.GetGlobalAddressList.side_effect = Exception("GAL unavailable")

        with patch.dict(sys.modules, com_mocks):
            from shared_tools.outlook_tool import fetch_outlook_contacts
            contacts = fetch_outlook_contacts()
            assert any(c["email"] == "dave@example.com" for c in contacts)

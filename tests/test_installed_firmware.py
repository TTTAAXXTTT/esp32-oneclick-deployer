"""Read-only firmware detection; hardware is simulated, never opened."""
import hashlib
import io
from pathlib import Path
import queue
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import MagicMock, patch
import esptool
import deployer_core as core
from main import Application
from test_deployer_core import PORT, DETECT_OUTPUT
import test_deployer_core as fixtures


def image():
    data = bytearray(b'\xff' * core.FLASH_BYTES)
    for i, (kind, offset, size) in enumerate([(1,0x9000,0x7000),(0,0x10000,0x10000),(0,0x20000,0x10000)]):
        struct.pack_into('<HBBII16sI',data,0x8000+i*32,0x50AA,kind,0,offset,size,b'partition',0)
    position=0x8060
    data[position:position+2]=b'\xeb\xeb'
    data[position+16:position+32]=hashlib.md5(data[0x8000:position]).digest()
    data[0]=data[0x10000]=0xe9
    return bytes(data)


class RegionTests(unittest.TestCase):
    def test_configuration_changes_do_not_affect_code_comparison(self):
        original=image(); changed=bytearray(original); changed[0x9000:0xA000]=b'a'*0x1000
        self.assertEqual(core.firmware_verification_regions(original),core.firmware_verification_regions(bytes(changed)))
        esp=MagicMock();esp.CHIP_NAME='ESP32-S3';esp.secure_download_mode=False;esp.IS_STUB=False
        esp.flash_md5sum.side_effect=lambda offset,size: hashlib.md5(changed[offset:offset+size]).hexdigest()
        with patch('esptool.cmds.detect_flash_size',return_value='16MB'),redirect_stdout(io.StringIO()):
            esptool.cmds.verify_flash(esp,core.firmware_verification_regions(original))
        esp.flash_write.assert_not_called()
        esp.flash_begin.assert_not_called()

    def test_all_app_slots_including_unused_slot_are_checked(self):
        data=image(); regions=core.firmware_verification_regions(data)
        self.assertEqual([a for a,_ in regions],[0,0x10000,0x20000])
        changed=bytearray(data); changed[0x20000]=0xe9
        self.assertNotEqual(regions,core.firmware_verification_regions(bytes(changed)))
        esp=MagicMock();esp.CHIP_NAME='ESP32-S3';esp.secure_download_mode=False;esp.IS_STUB=False
        esp.flash_md5sum.side_effect=lambda offset,size: hashlib.md5(changed[offset:offset+size]).hexdigest()
        with patch('esptool.cmds.detect_flash_size',return_value='16MB'),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(esptool.FatalError,'Verification failed'):
                esptool.cmds.verify_flash(esp,regions)

    def test_bad_table_digest_and_out_of_range_partition_rejected(self):
        for pos in (0x8004,0x8070):
            data=bytearray(image()); data[pos]^=0x80
            with self.subTest(pos=pos),self.assertRaises(core.DeployerError):core.firmware_verification_regions(data)

    def test_real_bundled_firmware_table_is_supported(self):
        data=(Path(__file__).resolve().parents[1]/'firmware/agent-n16r8.bin')
        if not data.exists(): self.skipTest('Build inputs not downloaded yet')
        self.assertGreater(len(core.firmware_verification_regions(data.read_bytes())),1)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.engine=core.Deployer(core.FirmwareSpec('ref.bin','a'*64),['program'])
        self.info=core.parse_device_output(DETECT_OUTPUT,PORT)

    def test_success_and_mismatch_require_explicit_worker_result(self):
        for token, expected in [('FIRMWARE_MATCH',True),('FIRMWARE_DIFFERENT',False)]:
            with self.subTest(token=token),patch.object(self.engine,'validate_firmware'),patch.object(self.engine,'_port_still_present'),patch.object(self.engine,'_run_esptool',return_value=token+'\n') as run:
                self.assertEqual(self.engine.verify_installed(PORT,self.info),expected)
                args=run.call_args.args[0]
                self.assertIn('verify-flash',args)
                self.assertNotIn('write-flash',args)
                self.assertNotIn('erase-flash',args)

    def test_connection_failure_is_not_reported_as_different_firmware(self):
        with patch.object(self.engine,'validate_firmware'),patch.object(self.engine,'_port_still_present'),patch.object(self.engine,'_run_esptool',side_effect=core.DeployerError('No serial data received')):
            with self.assertRaisesRegex(core.DeployerError,'No serial'):self.engine.verify_installed(PORT,self.info)

    def test_success_without_evidence_is_rejected(self):
        with patch.object(self.engine,'validate_firmware'),patch.object(self.engine,'_port_still_present'),patch.object(self.engine,'_run_esptool',return_value='done'):
            with self.assertRaisesRegex(core.DeployerError,'无法判断'):self.engine.verify_installed(PORT,self.info)

    def test_unguarded_worker_verification_is_refused(self):
        with redirect_stderr(io.StringIO()):
            self.assertNotEqual(core.esptool_entry(['verify-flash','0x0','ref.bin']),0)

    def test_worker_uses_read_only_api_and_exact_live_device(self):
        reference=image()
        for fail in (None,esptool.FatalError('Verification failed.'),esptool.FatalError('connection lost')):
            fake,esp=fixtures.GuardedEntryTests().fake_esptool()
            fake.FatalError=esptool.FatalError
            fake.cmds.verify_flash.side_effect=fail
            with tempfile.TemporaryDirectory() as directory:
                file=Path(directory)/'reference.bin';file.write_bytes(reference)
                args=['--chip','esp32s3','--port','COM3','--before','no-reset','verify-flash','0x0',str(file)]
                output=io.StringIO()
                with patch.dict(sys.modules,{'esptool':fake}),patch.object(core,'validate_firmware'),redirect_stdout(output):
                    if fail and str(fail)=='connection lost':
                        with self.assertRaisesRegex(esptool.FatalError,'connection lost'):core._guarded_write(args,'c4:9e:7e:18:c2:b0',hashlib.sha256(reference).hexdigest(),len(reference),verify_only=True)
                    else:
                        core._guarded_write(args,'c4:9e:7e:18:c2:b0',hashlib.sha256(reference).hexdigest(),len(reference),verify_only=True)
                        self.assertIn('FIRMWARE_DIFFERENT' if fail else 'FIRMWARE_MATCH',output.getvalue())
                fake.main.assert_not_called()
                fake.cmds.write_flash.assert_not_called()
                esp._port.close.assert_called_once()
                self.assertIs(fake.cmds.verify_flash.call_args.args[0],esp)

class GuiTests(unittest.TestCase):
    def app(self):
        app=Application.__new__(Application);app.closing=False;app.operation_kind='verify';app.events=queue.Queue()
        for name in ['window','status_title','status_detail','_log','_set_stage','_update_actions']:setattr(app,name,MagicMock())
        return app

    def test_connection_error_does_not_claim_flash_failure(self):
        app=self.app();app._show_error('No serial data received')
        self.assertEqual(app.status_title.configure.call_args.kwargs['text'],'无法判断板上固件')

    def test_mismatch_shows_other_version_message(self):
        app=self.app();app.events.put(('verified',False));app._drain()
        self.assertIn('不一致',app.status_title.configure.call_args.kwargs['text'])
        self.assertIn('其他版本',app.status_detail.configure.call_args.kwargs['text'])

    @patch('deployer_core.Deployer')
    def test_verify_worker_never_calls_flash(self,engine):
        app=self.app();app.spec=MagicMock();engine.return_value.verify_installed.return_value=True
        app._work('verify',PORT,'no-reset')
        engine.return_value.flash.assert_not_called()
        engine.return_value.verify_installed.assert_called_once()
        self.assertIn(('verified',True),list(app.events.queue))

if __name__=='__main__':unittest.main()

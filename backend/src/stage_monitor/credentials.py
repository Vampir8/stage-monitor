"""Per-device secrets protected by the current Windows user's credential vault."""
import ctypes
from ctypes import wintypes
import os


class CredentialUnavailable(Exception):
    pass


class Credential(ctypes.Structure):
    _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
               ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
               ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
               ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
               ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
               ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]


def reference(device_id):
    return 'StageMonitor/device/' + device_id


class WindowsCredentialStore:
    def api(self):
        if os.name != 'nt':
            raise CredentialUnavailable('Local password storage requires Windows')
        api = ctypes.WinDLL('advapi32', use_last_error=True)
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api

    def read(self, device_id):
        api = self.api()
        pointer = ctypes.POINTER(Credential)()
        if not api.CredReadW(reference(device_id), 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return None
            raise CredentialUnavailable('Windows credential read failed')
        try:
            return ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize).decode('utf-16-le')
        finally:
            api.CredFree(pointer)

    def write(self, device_id, password):
        encoded = password.encode('utf-16-le')
        if not encoded or len(encoded) > 2560:
            raise CredentialUnavailable('Invalid credential length')
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        record = Credential(Type=1, TargetName=reference(device_id),
                            CredentialBlobSize=len(encoded), CredentialBlob=blob,
                            Persist=2, UserName='StageMonitor')
        if not self.api().CredWriteW(ctypes.byref(record), 0):
            raise CredentialUnavailable('Windows credential write failed')

    def delete(self, device_id):
        if not self.api().CredDeleteW(reference(device_id), 1, 0) and ctypes.get_last_error() != 1168:
            raise CredentialUnavailable('Windows credential delete failed')

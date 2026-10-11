import keyring
from keyring.errors import KeyringError


class Vault:
    def __init__(self, namespace="swarm-workbench"):
        self.namespace = namespace
        self.memory = {}

    def status(self):
        backend = keyring.get_keyring()
        # Reject plaintext/encrypted third-party fallback stores; use native OS backends only.
        module = type(backend).__module__
        secure = module.startswith(("keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.kwallet"))
        return dict(persistent=secure, backend=type(backend).__name__,
            message="Keys are saved in your OS credential store." if secure else "No supported OS credential store. Use session-only keys; they expire when the server stops.")

    def set(self, id, secret, ephemeral=False):
        if not secret:
            return "none"
        if ephemeral:
            self.memory[id] = secret
            return "session"
        if not self.status()["persistent"]:
            raise ValueError("An OS credential store is unavailable. Select session-only storage.")
        try:
            keyring.set_password(self.namespace, id, secret)
        except KeyringError as e:
            raise ValueError("The OS credential store could not save this key. Try session-only storage.") from e
        return "os"

    def get(self, id):
        if id in self.memory:
            return self.memory[id]
        if self.status()["persistent"]:
            try:
                return keyring.get_password(self.namespace, id)
            except KeyringError:
                return None
        return None

    def delete(self, id):
        self.memory.pop(id, None)
        if self.status()["persistent"]:
            try:
                keyring.delete_password(self.namespace, id)
            except KeyringError:
                pass

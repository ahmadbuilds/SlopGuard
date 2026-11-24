"""Known Python import names and their PyPI distribution names."""

IMPORT_TO_DISTRIBUTION = {
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "jwt": "PyJWT",
    "dotenv": "python-dotenv",
    "Crypto": "pycryptodome",
    "attr": "attrs",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "magic": "python-magic",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "jose": "python-jose",
    "OpenSSL": "pyOpenSSL",
    "usb": "pyusb",
    "git": "GitPython",
    "github": "PyGithub",
    "telegram": "python-telegram-bot",
    "slugify": "python-slugify",
    "MySQLdb": "mysqlclient",
    "fitz": "PyMuPDF",
    "zmq": "pyzmq",
    "socks": "PySocks",
    "nacl": "PyNaCl",
    "dns": "dnspython",
    "kafka": "kafka-python",
    "googleapiclient": "google-api-python-client",
    "pkg_resources": "setuptools",
    "mpl_toolkits": "matplotlib",
    "win32api": "pywin32",
    "win32con": "pywin32",
    "ldap": "python-ldap",
    "google.protobuf": "protobuf",
    "google.cloud.storage": "google-cloud-storage",
    "google.generativeai": "google-generativeai",
    "azure.storage.blob": "azure-storage-blob",
    "azure.identity": "azure-identity",
}

NAMESPACE_ROOTS = frozenset({"google", "azure", "zope", "backports"})


def distribution_for_import(module_name: str) -> str | None:
    """Return a known distribution using the longest matching dotted prefix."""

    for prefix in sorted(IMPORT_TO_DISTRIBUTION, key=len, reverse=True):
        if module_name == prefix or module_name.startswith(f"{prefix}."):
            return IMPORT_TO_DISTRIBUTION[prefix]
    return None

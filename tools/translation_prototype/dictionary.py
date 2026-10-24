"""Prototype path adapter; lookup and bounded worker are shared with production."""
from pathlib import Path
import sys
from src.services.dictionary_lookup import Dictionary as _Dictionary, DictionaryClient as _Client
from src.services.dictionary_lookup import Entry, Lookup, normalize


def dictionary_path():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / 'dictionary.sqlite3'
    return Path(__file__).resolve().parents[2] / 'qa/dictionary-prototype-20261022-1927/app/dictionary.sqlite3'


class Dictionary(_Dictionary):
    def __init__(self, path=None, supplements=None):
        if supplements is None:
            supplements = (Path(sys.executable).parent / 'dictionary_supplements.json'
                           if getattr(sys, 'frozen', False)
                           else Path(__file__).with_name('dictionary_supplements.json'))
        super().__init__(path or dictionary_path(), supplements)


class DictionaryClient(_Client):
    def __init__(self, parent=None, store=None, cache_size=128):
        super().__init__(parent, store or Dictionary(), cache_size)

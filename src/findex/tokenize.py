import re
import unicodedata
from pathlib import Path
from itertools import islice
from findex.corpus import iter_documents

def tokenize(text):
    text = unicodedata.normalize('NFC', text)
    text = text.casefold()
    for match in re.finditer(r'\w+', text):
        yield match.group(0)

if __name__ == "__main__":
    doc = next(iter_documents(Path("data/dota2wiki.jsonl")))
    tokens = list(islice(tokenize(doc.text), 20))
    print(tokens)
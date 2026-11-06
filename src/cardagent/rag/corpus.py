"""Load the policy corpus and split it into citable chunks (one per `## ` section)."""

import re
from dataclasses import dataclass
from pathlib import Path

CORPUS_DIR = Path("corpus/policies")
_SECTION = re.compile(r"^## (\d+)\.\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str  # "es/perdida_robo#3": the citation key
    doc_id: str  # "es/perdida_robo"
    lang: str
    doc_title: str
    section: str  # "3. Reposición después de un robo"
    text: str

    def for_embedding(self) -> str:
        return f"{self.doc_title}. {self.section}. {self.text}"


def parse_document(doc_id: str, lang: str, markdown: str) -> list[Chunk]:
    title = markdown.splitlines()[0].lstrip("# ").strip()
    matches = list(_SECTION.finditer(markdown))
    chunks = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[m.end() : end].strip()
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#{m.group(1)}",
                doc_id=doc_id,
                lang=lang,
                doc_title=title,
                section=f"{m.group(1)}. {m.group(2).strip()}",
                text=body,
            )
        )
    return chunks


def load_corpus(root: Path = CORPUS_DIR) -> list[Chunk]:
    chunks = []
    for path in sorted(root.glob("*/*.md")):
        lang = path.parent.name
        chunks += parse_document(f"{lang}/{path.stem}", lang, path.read_text(encoding="utf-8"))
    return chunks

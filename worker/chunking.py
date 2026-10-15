"""Token-bounded chunks prefer paragraph/sentence boundaries and keep provenance."""
import bisect
import re
from dataclasses import dataclass

CHUNKER_VERSION = 'semantic-token-chunker-v1'


@dataclass
class StructuredChunk:
    content: str
    page: int | None
    section: str | None
    section_idx: int
    offset_start: int
    offset_end: int
    token_count: int
    embedding_text: str


def chunk_document(document, tokenizer, max_tokens=96, overlap_tokens=12, model_max_tokens=128):
    if max_tokens <= 0 or not 0 <= overlap_tokens < max_tokens:
        raise ValueError('Require positive chunk size and overlap smaller than chunk size')
    if not tokenizer.is_fast:
        raise ValueError('Chunk provenance requires a fast tokenizer with offset mappings')
    chunks = []
    special = tokenizer.num_special_tokens_to_add(pair=False)
    for section_idx, source in enumerate(document.sections):
        prefix_ids = tokenizer.encode(document.title + '\n' + (source.section or ''), add_special_tokens=False)
        prefix = tokenizer.decode(prefix_ids[:min(32, model_max_tokens // 4)], skip_special_tokens=True).strip() + '\n\n'
        prefix_tokens = len(tokenizer.encode(prefix, add_special_tokens=False))
        budget = min(max_tokens, model_max_tokens - special - prefix_tokens - 2)
        if budget <= overlap_tokens:
            raise ValueError('Chunk overlap exceeds available embedding token budget')
        encoded = tokenizer(source.text, add_special_tokens=False, return_offsets_mapping=True,
                            truncation=False, verbose=False)
        offsets = encoded['offset_mapping']
        # Some tokenizers expand one Unicode character into several tokens at the same offset.
        starts, ends = [pair[0] for pair in offsets], [pair[1] for pair in offsets]
        boundaries = [match.end() for match in re.finditer(r'\n\s*\n|(?<=[.!?])\s+', source.text)]
        start = 0
        while start < len(offsets):
            end = min(start + budget, len(offsets))
            if end < len(offsets):
                candidates = [position for position in boundaries if ends[min(start + budget // 2, end - 1)] <= position <= ends[end - 1]]
                if candidates:
                    end = bisect.bisect_right(ends, candidates[-1])
                # Never split tokens representing a single character across adjacent chunks.
                while end < len(offsets) and end > start + 1 and starts[end] < ends[end - 1]:
                    end -= 1
            begin, finish = starts[start], ends[end - 1]
            content = source.text[begin:finish]
            if len(tokenizer.encode(prefix + content, add_special_tokens=True)) > model_max_tokens:
                raise ValueError('Embedding input exceeds model token limit')
            chunks.append(StructuredChunk(content, source.page, source.section, section_idx,
                                          begin, finish, end - start, prefix + content))
            if end == len(offsets):
                break
            next_start = max(start + 1, end - overlap_tokens)
            while next_start > start and starts[next_start] < ends[next_start - 1]:
                next_start -= 1
            start = max(start + 1, next_start)
    return chunks

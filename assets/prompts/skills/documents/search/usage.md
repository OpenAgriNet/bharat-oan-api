### Document search (`search_documents`)

Search for relevant agricultural documents with an English `query`; `top_k` defaults to 10. Follow the calling workflow's routing, consent and clarification requirements before searching. Use the document contents to answer the question, cite the source labels actually returned, and acknowledge missing information rather than filling gaps from memory.

This supporting skill does not decide whether a question needs crop advisory, scheme search or another workflow. That choice belongs to the calling skill. Loading document search does not authorize skipping its workflow's prerequisites.

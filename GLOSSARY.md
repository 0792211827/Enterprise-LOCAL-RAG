# Enterprise Local RAG

An on-premises retrieval-augmented answering platform. Company documents, prompts and model weights stay inside the company network; models are reached through pluggable inference endpoints.

## Language

**Knowledge Base**:
A named collection of documents that is searched as one unit and is the unit of access control.
_Avoid_: Collection, index, corpus

**Document**:
A single source ingested into exactly one Knowledge Base: a file, or a web page fetched from a URL. It keeps its identity when its content is replaced.
_Avoid_: File, upload

**Image description**:
Text a vision model writes about an image inside a Document, together with the page or slide it sits on. It is searched and cited like the Document's own text.
_Avoid_: Caption, alt text, OCR

**Application**:
A named, admin-configured way of answering questions: it attaches Knowledge Bases to a prompt, a model choice and the platform's retrieval settings (set by the operator, not by admins).
_Avoid_: Bot, assistant, agent, app

**Grant**:
Permission for a Principal to read a Knowledge Base. Grants only add access.
_Avoid_: ACL, share

**Principal**:
Whoever a Grant names: a group, role, subject or everyone, as asserted by the company's identity provider. The platform does not own users.
_Avoid_: User, account

**Document restriction**:
An admin-set list of Principals on a Document that narrows who may read it within its Knowledge Base. It can never widen access beyond the Knowledge Base's Grants; an unrestricted Document inherits them.
_Avoid_: Document ACL, document grant, tag

**Conversation**:
A private, multi-turn exchange between one End user and one Application, readable only by its owner.
_Avoid_: Chat session, thread

**Attachment**:
A file or image an End user adds to a message in a Conversation; private to that Conversation and removed with it. A document too long for the prompt is *searched* instead: its chunks sit in a private partition of the chunk index that only its owner's Conversation can query.
_Avoid_: Upload, chat file

**Connector**:
A scheduled job that mirrors a source folder into one Knowledge Base, adding, updating and removing Documents to match.
_Avoid_: Integration, sync source, crawler

**Golden set**:
A versioned list of questions with the passages that must (or must not) be returned, optionally run as a named Principal, used to score an Application.
_Avoid_: Test set, benchmark

**End user**:
An employee who asks questions of an Application and never administers it.
_Avoid_: Querier, customer

**Admin**:
A person who manages Knowledge Bases, Documents, Applications, Grants and keys.
_Avoid_: Operator, superuser

**Inference endpoint**:
A network address serving a chat, embedding or reranking model, which can be swapped without changing the platform.
_Avoid_: Backend, provider URL

**Citation**:
A numbered pointer from an answer to the passage of a Document, or of an Attachment, that it drew on. The reader can open it to see the passage in its source.
_Avoid_: Source, reference, footnote

**Audit log**:
The compliance record of who did what, kept for the long term and read by auditors. It names people. It holds digests of the prompt and the answer, never their text, unless an operator sets `AUDIT__LEVEL=full`. Each event records the request ID and, when Telemetry is on, the trace ID.
_Avoid_: Activity log, history

**Telemetry**:
Short-lived measurements of how the platform behaves (counts, durations, errors, token totals, per Application and Knowledge Base) read by operators. It never names a person and never holds prompt, answer or document text. The trace of a request carries its request ID, the same ID the Audit log records; the request ID is never a metric label. Telemetry is kept 15 days (metrics) and 3 days (traces) by default, and a collector that is down never affects an answer.
_Avoid_: Monitoring, analytics, logs

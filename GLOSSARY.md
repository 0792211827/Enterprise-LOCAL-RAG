# Enterprise Local RAG

An on-premises retrieval-augmented answering platform. Company documents, prompts and model weights stay inside the company network; models are reached through pluggable inference endpoints.

## Language

**Knowledge Base**:
A named collection of documents that is searched as one unit and is the unit of access control.
_Avoid_: Collection, index, corpus

**Document**:
A single source file ingested into exactly one Knowledge Base.
_Avoid_: File, upload

**Application**:
A named, admin-configured way of answering questions: it attaches Knowledge Bases to a prompt, a model choice and a retrieval profile.
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
A file or image an End user adds to a message in a Conversation; private to that Conversation and removed with it.
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

**Rating**:
A thumbs up or down, with an optional comment, that an End user gives one answer in a Conversation. Admins see ratings without any Conversation text.

**Shared exchange**:
One question and its answer that an End user explicitly chose to share with admins when rating it; the only Conversation text an admin can read, and exportable as Golden set candidates.

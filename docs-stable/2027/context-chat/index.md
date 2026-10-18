# Context Chat

Status: private beta, application 0.5.0 with Client 0.0.481 or later. Availability
depends on the version selected by the owner of your local or self-hosted node.

Context Chat keeps projects, separate chats, documents and editable memory on
your node. It uses your connected personal ChatGPT account for text responses.
Available models and tools depend on that account and the provider. Connecting
an account does not import chats, memory or Projects from the ChatGPT website.

This release supports one installation owner's workspace. Separate private
project lists and ChatGPT connections for other subnet users are not implemented.
Guest access is not a supported mode. The beta is not qualified for a shared
subnet with separate user workspaces.

## Start a Conversation

Open Context Chat, connect your account using the provider's sign-in page, and
create a project. Each project has a main chat; create more chats for separate
tasks or documents. Existing projects keep their old conversation in the main
chat after upgrading.

Choose a model above the conversation. Where supported capabilities are known,
the reasoning selector offers that model's available effort choices. The
provider-default option leaves effort selection to the provider. Missing
capability information is shown as unknown; it does not imply that every effort
value is supported.

Model, reasoning and web-search settings belong to the selected chat. Project
defaults apply to future chats. Changing settings does not change a request
already running. After changing the connected account, explicitly confirm the
current account in the chat's model settings before continuing.

Use **+** to attach files or enable web search for the chat. You can also drop
files over the conversation. Read answers with Markdown, tables, code and
formulas; Copy keeps the original Markdown, with a plain-text copy option.

Search, rename or archive chats from the project list. Drafts stay with their
chat. The main chat can be archived; other chats can also be deleted after
confirmation. Deleting a chat keeps the project's document library.

## Documents and Context

Supported uploads are PDF, DOCX, XLSX, TXT, Markdown, JSON, CSV, HTML and LOG.
The common file limit is **10 MiB per file**. Extracted text is also bounded;
a document with more than 160,000 extracted characters must be split.

DOCX extraction preserves selected Markdown structure, and XLSX extraction
produces sheet/cell tables. PDF uses its text layer. Scanned pages, embedded
pictures, OCR and exact page-layout preservation are outside this beta. A large
file containing pictures can upload successfully without those pictures being
understood by the model. Encrypted, corrupt or textless documents receive an
error rather than a claimed successful reading.

Uploading stores a source in the project's library and selects it for the
addressed chat. Other chats do not automatically use it. For student reviews,
use a separate chat per student and explicitly select any shared grading rubric.
Manual source-selection presets help reuse the same rubric or reference set.

The context inspector shows selected sources and their versions, memory
revisions and context-budget information. The application assembles selected
text, memory and a bounded recent history window itself. This beta does not
provide automatic retrieval over an entire library or hosted ChatGPT Projects.
If selected materials exceed the budget, reduce the selection before sending.

Each chat has separate derived memory. Project-shared memory requires an
explicit confirmation that its contents may be used by every chat in the
project. Inspect it before sharing facts from a student's work.

Source edits create versions. A chat using an earlier version retains that
selection until it is changed. Deleting a source removes its readable versions
and archived original, invalidates affected context and blocks retries that
require the deleted material. Answers already produced remain in chat history.

## Original Files, History and Recovery

By default, uploads retain extracted text. Enable original-file retention
before uploading if you also need the original binary for download/export.
This applies to future uploads; an earlier text-only upload cannot be restored
as its original file. Turning retention off purges archived original binaries.
Review the confirmation before changing that setting.

Long conversations use bounded pages, normally 50 messages, with previous,
newer and latest controls. The full conversation remains in storage and in an
authorized project export. The model receives a bounded context window, so it
does not reread the entire history on every turn.

Retry creates another attempt using the original request's saved model,
reasoning, tools and context snapshot. The earlier partial or failed attempt
is retained. To use changed settings or context, send a new message. A revoked
account, unavailable setting or deleted required source can prevent a retry;
the application does not silently substitute another account or model.

Beta upgrades use an isolated data snapshot. Review the beta before accepting
it into the working installation. Account credentials are not included in a
project export.

## Beta Limits

Telegram sharing/mirroring, read aloud, conversational project navigation, MCP,
OCR and image/audio/video inference are not available in this release. Browser
clipboard access depends on browser permissions. Remote access also requires a
connected node and an authorized owner session; a local successful check does
not establish that every remote route is available.

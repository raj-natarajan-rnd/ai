# DSS POC Plan

**Goal:** let people find the right dataset or document by asking a question in
plain English.

We build it in stages. Each stage is useful on its own, so we can stop or change
direction at any point without wasting what came before.

---

## Phase 1 - Search our dataset list

**Done.**

- Drop a JSON file into S3 and it appears in search within seconds
- Search copes with typos, knows "client" means "customer", and finds an exact
  table name when you type one
- Filter by owner, domain, size or date, and get counts by category
- Different kinds of data go into separate indexes so results do not get mixed up
- Field definitions are held in S3 and can be changed without a code release
- Failed uploads are captured for review rather than lost

**AWS services:** Amazon S3 (files), AWS Lambda (loading and querying), Amazon
OpenSearch Service (the search engine), AWS Secrets Manager (password), Amazon SQS
(failed uploads), Amazon CloudWatch (logs), Amazon VPC (private network).

Cost: about $27 a month.

---

## Phase 2 - Add documents

- Handle PDF, Word and Excel, not just JSON
- Pull the text out so it can be searched
- Break long documents into sections, so later we can point at the right paragraph
  rather than the whole file
- Keep a link back to the original file

**AWS services:** no new ones. Reading PDF and Word files is a feature of Amazon
OpenSearch Service that is already switched on. One extra AWS Lambda to load them.

One thing we must get right up front: a setting needed for Phase 3 can only be
switched on when an index is first created. If we miss it, Phase 3 means rebuilding
and reloading everything.

**Finished when:** searching a phrase returns the document and the section it came from.

---

## Phase 3 - Search by meaning

Today search matches words. This makes it match meaning, so "how do we track
customers leaving" finds the churn dataset even though those words are not in it.

- Uses AWS Bedrock to do the heavy lifting, so there is no new service to run
- We keep word matching alongside it. Meaning-based search on its own gets exact
  table names wrong, and people type table names all day

**AWS services:** adds Amazon Bedrock (the AI model that reads meaning). OpenSearch
calls Bedrock itself as files are loaded, so there is nothing new for us to run.

Needs a larger server. Cost goes from about $27 to about $90 a month.

**Finished when:** a question worded differently from the data still finds it.

---

## Phase 4 - The chatbot

- Ask a question, get an answer in plain English
- Every answer shows which datasets or documents it came from
- It can filter and count, not just find similar text, so "which finance datasets
  over a million rows mention churn, and who owns them" works
- If nothing good is found it says so, instead of guessing

**AWS services:** Amazon Bedrock (the chat model) and one more AWS Lambda to sit
between the chat and the search engine.

**Finished when:** someone can ask a real question and trust the answer.

---

## Phase 5 - Ready for real users

- Web page with a search box, filters, results and the chat panel
- Login, with people only seeing what they are allowed to see
- Alerts when uploads fail
- Security tightened from POC settings to something we would leave running

**AWS services:** adds Amazon Cognito (login), Amazon API Gateway (the web address
the page talks to), Amazon S3 and CloudFront (hosting the page), and Amazon SNS
(alerts).

**Finished when:** someone outside the team can use it without help.

---

## What we run, in one list

| Service | What it does for us | From |
|---|---|---|
| Amazon S3 | Holds the files we index, and the web page later | Phase 1 |
| AWS Lambda | Loads files, answers queries, runs the chat | Phase 1 |
| Amazon OpenSearch Service | The search engine, and later the vector store | Phase 1 |
| AWS Secrets Manager | Holds the search engine password | Phase 1 |
| Amazon SQS | Catches failed uploads | Phase 1 |
| Amazon CloudWatch | Logs and alerts | Phase 1 |
| Amazon VPC | Keeps the search engine off the public internet | Phase 1 |
| Amazon Bedrock | Understands meaning, then answers questions | Phase 3 |
| Amazon Cognito | Login and permissions | Phase 5 |
| Amazon API Gateway | Front door for the web page | Phase 5 |
| Amazon CloudFront | Serves the web page | Phase 5 |
| Amazon SNS | Sends alerts | Phase 5 |

Nothing here is a new product to buy or a new platform to run. All of it is
standard AWS, billed by usage.

---

## Decisions already made

- **Managed OpenSearch, not the serverless version.** Serverless cannot read PDFs
  and Word files, which rules it out for Phase 2.
- **The search service sits inside our private network.** More secure, but it needs
  VPN or similar to reach. This cannot be changed later without rebuilding.
- **S3 holds the source of truth.** The search index is disposable and can be
  rebuilt from S3 at any time, so nothing is lost if we start over.
- **We control the search ourselves rather than using an off-the-shelf AI service.**
  Off-the-shelf only finds similar text. A catalogue needs filtering, counting and
  exact-name lookup as well.

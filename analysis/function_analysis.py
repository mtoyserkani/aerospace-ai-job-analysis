"""
function_analysis.py - Job-function-level skill DISCOVERY for job seekers.

This script is discovery-only. It does NOT score jobs against pre-built
keyword files (governance.txt, capability.txt, certification_adjacent.txt,
cybersecurity_skills.txt) - that hypothesis-testing job belongs to
keyword_analysis.py, scoped to the whole dataset, for the Article B thesis.

This script:
  1. Prompts you (interactively, no file editing required) for the job
     titles that define your function - e.g. "program manager, project
     manager" - matched with fuzzy, token-based matching (word order and
     small typos don't matter, but distinct words like "project" vs
     "product" are NOT treated as typos of each other - see _tokens_match).
     What you type is optionally saved to job_functions/<name>.txt so next
     time you can skip the prompt by passing --function <name>. Pass
     --label "Display Name" for a clean section header.
  2. Optionally prompts for your own ad hoc keywords to check (skippable -
     press enter for none). This is YOUR hypothesis, checked fresh each
     run, separate from discovery and separate from Article B's governance
     keyword files. Reported in its own "USER KEYWORD CHECK" section.
  3. Within the matched bucket, reports:
       - total jobs matched / total jobs in dataset (with %)
       - seniority breakdown (% of jobs matched in this function)
       - salary by seniority, US POSTINGS ONLY, in USD. Non-US postings
         (Canada, UK, Europe, India, Australia, Germany, etc. - this
         dataset mixes country labels, e.g. both "United States of
         America" and "US" appear, both are treated as US) are excluded
         from salary math rather than silently averaged in. Excluded
         count is shown so nothing is hidden.
       - top companies hiring for this function
       - your ad hoc keyword hits, if any
       - CERTIFICATIONS - small maintained list of real, nameable certs
         (PMP, CISSP, Six Sigma, etc - see CERTIFICATIONS list below).
         Count, %, top companies per cert.
       - SECURITY CLEARANCES - small maintained list of real US clearance
         tiers (Top Secret, TS/SCI, Secret, Public Trust, etc - see
         CLEARANCES list below). Count, %, top companies per tier.
       - TOOLS & SOFTWARE - matched against the O*NET Software Skills
         database (31,821 total rows across all 923 O*NET occupations,
         filtered down to Hot Technology=Y only - see load_onet_tools.
         Unfiltered, the list includes thousands of generic category
         descriptions like "Database reporting software" or "Security
         testing software" that aren't real product names and were
         dominating results with 95%+ false match rates in testing.
         Filtering to Hot=Y trades some completeness (a real but obscure
         or older tool may lack the Hot flag) for much higher precision.
         CC BY 4.0, U.S. Dept. of Labor / O*NET - see
         https://www.onetcenter.org/database.html). The full filtered
         list is used regardless of which occupation O*NET ties a tool
         to - function-bucket scoping (you've already filtered to your
         job titles) does the real filtering, not the O*NET occupation
         code. Matching reuses the same fuzzy token logic as job-function
         title matching (_tokens_match), but with a looser rule: ANY
         core token match counts (not ALL, unlike job-function matching),
         so a posting saying just "Adobe" still matches "Adobe Acrobat" -
         at a lower reported match strength. Match strength (1.0 = every
         core token found, <1.0 = partial/brand-only) is shown so you can
         judge confidence yourself. Generic words ("software", "Inc",
         "Corp", "Corporation", "Systems") are stripped from tool names
         before matching so they don't inflate scores.

Requires data/reference/onet_software_skills.txt to exist (tab-delimited,
as downloaded from O*NET - see SKILL.md or project handoff for the exact
curl command). If missing, the Tools & Software section is skipped with
a message telling you how to get it, rather than failing.

A job can match more than one function bucket (e.g. "Cybersecurity Program
Manager" matches both cybersecurity and program_management terms). This is
intentional - crossover roles are informative, not noise to be removed.

Usage:
    python3 analysis/function_analysis.py
        (prompts you for everything - job function, optional keywords)
    python3 analysis/function_analysis.py --function cybersecurity
        (skips the job-function prompt, reuses job_functions/cybersecurity.txt;
         still prompts for optional ad hoc keywords unless --no-prompt-keywords)
    python3 analysis/function_analysis.py --function program_management --label "Product Management"
        (clean display name in the report header instead of the saved filename)
    python3 analysis/function_analysis.py --function cybersecurity --no-prompt-keywords
    python3 analysis/function_analysis.py --input data/master_dataset.csv --export data/function_results.csv
"""

import argparse
import html
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


JOB_POSTING_STOPWORDS = {
    "experience", "ability", "strong", "team", "responsible", "responsibility",
    "responsibilities", "skills", "skill", "knowledge", "work", "working",
    "years", "year", "required", "preferred", "plus", "candidate", "candidates",
    "position", "role", "job", "qualified", "qualifications", "qualification",
    "applicants", "applicant", "apply", "application", "please", "include",
    "including", "minimum", "maximum", "basic", "demonstrated", "proven",
    "excellent", "outstanding", "highly", "must", "ideal", "ideally", "looking",
    "seeking", "join", "environment", "fast", "paced", "dynamic", "innovative",
    "collaborative", "communication", "verbal", "written", "interpersonal",
    "detail", "oriented", "self", "starter", "motivated", "passionate",
    "across", "within", "such", "various", "other", "ensure", "ensuring",
    "support", "supporting", "perform", "performing", "performs", "develop",
    "developing", "develops", "provide", "providing", "provides", "help",
    "helps", "make", "makes", "use", "uses", "using", "new", "also", "well",
    "good", "high", "level", "levels", "related", "field", "degree", "bachelor",
    "master", "equivalent", "combination", "based", "able", "willing",
    "duties", "tasks", "etc",
}

ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "as", "at", "by", "this", "that", "from", "will",
    "you", "your", "we", "our", "have", "has", "may", "can", "all", "more",
    "their", "they", "it", "its", "than", "into", "if", "not", "but", "who",
    "what", "when", "where", "how", "which", "while", "during", "between",
}

# Generic words stripped from O*NET tool names before matching, so
# "SAP software" reduces to just {sap} and doesn't require literally
# matching the word "software" too.
GENERIC_TOOL_WORDS = {
    "software", "inc", "corp", "corporation", "systems", "system",
    "technologies", "technology", "solutions", "solution", "company",
    "the",
}

SENIORITY_ORDER = ["Junior", "Mid", "Senior", "Lead", "Principal", "Manager", "Director"]

# US country labels are inconsistent in this dataset - both full name and
# abbreviation appear. Both are treated as US for salary scoping.
US_COUNTRY_LABELS = {"United States of America", "US"}

# Small, maintained, real-world lists - not discovered, because both
# certifications and clearances are closed/nameable vocabularies where a
# maintained list is more reliable than open-ended phrase discovery
# (see project notes on why bigram discovery fragments proper nouns).
#
# AI-specific certifications added below are confirmed real via direct
# verification (web search against primary sources - Google Cloud's own
# certification page, Anthropic's own announcement), not from a scraped
# badge aggregator (Credly) or community dataset - see project notes on
# why those sources were rejected (ToS/scraping risk, synthetic/unverified
# data). "Generative AI Leader" and "Professional Machine Learning
# Engineer" are Google Cloud's current two AI certification pillars as of
# 2026. "Claude Certified Architect" launched March 12, 2026 - Anthropic's
# first official technical certification, confirmed via Anthropic's own
# anthropic.com/news announcement. Checked against this dataset: all
# three return 0-4 hits currently, which is itself a finding (these
# postdate most of the scrape, or aerospace hasn't adopted them yet) -
# zero-count entries are kept rather than silently dropped.
CERTIFICATIONS = [
    "PMP", "CAPM", "PgMP", "PMI-ACP", "CSM", "CSPO", "Six Sigma",
    "Lean Six Sigma", "CISSP", "CISM", "CISA", "Security+", "CompTIA Security+",
    "ITIL", "PE", "CCNA", "AWS Certified", "Scrum Master", "Agile Certified",
    "FAA Certificate", "DAWIA",
    # Note: "A&P License" removed from here - it's the same credential as
    # "A&P" in AEROSPACE_CERTIFICATIONS below, kept there only to avoid
    # double-counting one credential under two different phrasings.
    # --- AI-specific certifications, added after confirming real via
    # direct source verification (see comment above) ---
    "Generative AI Leader", "Professional Machine Learning Engineer",
    "Claude Certified Architect", "TensorFlow Developer Certificate",
    # --- round 2: AI/data-engineering platform certifications and
    # Pragmatic Institute's real AI PM certification, all verified real
    # via direct source check. "Reforge" deliberately NOT added here -
    # confirmed via source check that Reforge explicitly describes itself
    # as not a traditional certification, but a membership/community
    # product - listing it as a certification would be inaccurate, even
    # though it's a real and relevant credential-adjacent program.
    # "CPM" (Certified Product Manager) deliberately NOT added - checked
    # real context, found exclusively used as "Critical Path Method," an
    # unrelated scheduling term, in every sample found in this dataset.
    "AI Product Management Expert Certification",
    "Databricks Certified Generative AI Engineer",
    "AWS Certified Data Engineer", "Azure AI Engineer Associate",
    "Google Cloud Professional Data Engineer",
    "Google Cloud Professional Machine Learning Engineer",
    "Microsoft Certified: AI Business Professional",
    # --- round 3: design thinking / product strategy certifications,
    # folded in from a previously separate DESIGN_THINKING_CERTIFICATIONS
    # list (removed per request - kept here instead of its own section).
    # Each verified real via direct source check (IDEO U's own
    # ideou.com/products pages, LUMA Institute's own luma-institute.com
    # program pages). Note for accuracy: most of these are completion-
    # based certificates (finish the required courses, receive the
    # certificate) rather than pass/fail proctored exams like CISSP or
    # PMP - worth knowing if writing about the distinction. ---
    "IDEO U", "Design Thinking Certificate", "AI x Design Thinking",
    "Stanford d.school", "MIT Sloan Design Thinking",
    "IBM Enterprise Design Thinking", "LUMA Institute",
    "Certified Human-Centered Design Practitioner",
    "Harvard Business School Design Thinking", "Human-Centered Design",
    "Double Diamond", "Design Sprint",
    # --- round 4: PM/data-analyst/data-engineering/software-engineering/
    # QA/MLOps certifications, each verified real and checked against the
    # real dataset before inclusion. RHCE (42), CKA (40), CKAD (38), and
    # Selenium (103) are substantial real signals - bigger than most
    # entries already in this list. "AIPMM" and "Spring Certified"
    # checked and returned 0 hits - included anyway per the standing
    # zero-count-inclusion rule, not excluded. ---
    "AIPMM", "Product School", "ISTQB", "ASQ Certified Software Quality Engineer",
    "Selenium", "RHCE", "CKA", "CKAD", "Spring Certified Professional",
    "Oracle Certified Professional", "Java SE", "PL-300",
    "Tableau Desktop Certified", "CompTIA Data+", "Snowflake Core Certification",
    "Databricks Certified Data Engineer", "Databricks Certified Machine Learning Professional",
    "AWS Certified Solutions Architect", "AWS Certified DevOps Engineer",
    "Google Cloud Professional Cloud Architect", "Azure Solutions Architect Expert",
]

CLEARANCES = [
    "Top Secret", "TS/SCI", "TS SCI", "Secret Clearance", "Secret",
    "Public Trust", "Confidential Clearance", "Interim Clearance",
    "Security Clearance", "SSBI", "Polygraph",
]

# Aerospace/defense-specific certifications, separate from the general
# PM/IT list above. Each verified real via a direct source check (e.g.
# INCOSE's own incose.org/certification page) before inclusion - not
# trusted wholesale from the document that prompted this list, which
# also contained unverifiable company-attribution claims ("heavily
# required at Lockheed Martin", "extremely common at Blue Origin", etc).
# Those claims are NOT carried into this list or any code comment below,
# since they could not be independently verified and read as plausible-
# sounding fabrication rather than sourced fact - this project's own
# rule is "numbers and primary sources, or it didn't happen." Checked
# against the real dataset before being added: AS9100 (849 hits) and
# GD&T (1,073 hits) are large, previously-unreported signals - bigger
# than most things already in the original CERTIFICATIONS list above.
AEROSPACE_CERTIFICATIONS = [
    "INCOSE", "ASEP", "CSEP", "ESEP", "AS9100", "AS9110", "NDT", "ASNT",
    "GD&T", "CWI", "CQE", "CQA", "A&P", "GROL", "SAFe", "PSM",
    "Earned Value Management", "IPC-A-610", "IPC-J-STD-001",
    # --- round 2: automotive/aerospace quality standards found via the
    # AV crossover document, verified large and real in this dataset
    # (APQP=207, PPAP=141 - bigger than most existing entries above) ---
    "APQP", "PPAP",
    # --- round 3: selected AI-governance regulatory-standards terms,
    # moved here from keywords/governance.txt (Article B's whole-dataset
    # hypothesis-testing list) because they are real, named, certifiable
    # standards/documents - not abstract governance vocabulary like
    # "responsible AI" or "model risk," which stay exclusively in
    # governance.txt. "SOTIF" and "ODD" deliberately excluded from this
    # addition - both already exist in AV_AEROSPACE_CROSSOVER below, and
    # duplicating them here would risk two different numbers for the
    # same term depending on which script/list ran. These 8 terms are
    # near-universally zero in this dataset, which is itself the Article
    # B finding (zero AI-governance language across aerospace postings) -
    # kept here too, not just in governance.txt, since this section's
    # job-function-scoping makes the absence visible per-role, not just
    # dataset-wide. ---
    "ARP6983", "EASA AI Roadmap", "FAA AI Safety", "DO-178C AI",
    "ODD Definition", "Operational Design Domain", "Learning Assurance",
    "DAL-AI", "Design Assurance Level AI",
    # --- round 4: infrastructure/data-compliance frameworks, checked
    # against the real dataset before inclusion. ITAR (3,353 hits, over
    # 13% of the entire 25,474-job dataset) is the single largest finding
    # in this whole project - bigger than every other entry across every
    # list built so far. FedRAMP (119), GovCloud (106), and SCIF (80) are
    # also substantial. These are compliance FRAMEWORKS an infrastructure
    # or program meets, not credentials a person earns - placed here
    # rather than in a tools list since they're fundamentally about
    # regulatory/security posture, the same theme as AS9100/GD&T above. ---
    "ITAR", "FedRAMP", "GovCloud", "DoD Impact Level", "SCIF",
    # --- round 5: traditional aerospace deterministic-software/quality
    # standards, checked against real data before inclusion. "AS9100"
    # was already in this list (added round 1) - not duplicated here.
    # DO-178C (108), DO-254 (160), and ISO 9001 (315) are substantial
    # real signals, bigger than several entries already present. ---
    "DO-178C", "DO-254", "ARP4754A", "ARP4761", "MIL-HDBK-516C",
    "MIL-STD-882E", "ISO 9001", "Type Certificate",
    "Supplemental Type Certificate",
]

# Terms that must be matched CASE-SENSITIVELY because the lowercase form
# collides with a common English word. Found via direct verification:
# "SAFe" (Scaled Agile Framework methodology, capitalized this way by
# convention) returned 471 real hits case-sensitive vs 9,191 if matched
# case-insensitively against "safe" (the ordinary word, as in "safe
# operations," "safety-critical") - a 20x inflation that would have
# silently corrupted this number if not caught. Other short acronyms
# checked at the same time (PSM, CQA, GROL, CWI, NDT, CSEP) did NOT show
# this problem - it's specific to terms that happen to spell a real word.
# "LeSS" (Large-Scale Scrum) is an even more extreme version of the same
# problem: case-insensitive matching against "less" (one of the most
# common words in English) returned 1,722 hits; case-sensitive "LeSS"
# returns the real number - just 1.
CASE_SENSITIVE_TERMS = {"SAFe", "LeSS"}

# Prototyping/collaboration/PM tools missing from O*NET coverage - same
# gap pattern as AI_DATA_TOOLS below, just for product/program management
# and design workflows instead of AI/data engineering.
#
# IMPORTANT: every term below is included regardless of how many hits it
# returns in THIS aerospace dataset specifically. An earlier version of
# this list excluded Productboard, Aha!, Pendo, and Mixpanel because they
# returned 0 hits here - that was the wrong call, flagged correctly: this
# article's readers work across many industries, and a tool that's dead
# in aerospace may be standard in SaaS or consumer product management.
# Zero-count in one dataset is itself a reportable data point ("not used
# in aerospace"), not a reason to remove the term from the lookup list
# entirely. The verification bar stays the same (every term must be a
# real, named product, confirmed before inclusion) - only the "must also
# return a nonzero count in this one dataset" bar has been dropped.
PM_DESIGN_TOOLS = [
    "Smartsheet", "Figma", "Miro", "Canva", "Notion", "Lucidchart",
    "Mural", "Amplitude", "Productboard", "Aha!", "Pendo", "Mixpanel",
    # --- AI-PM-specific research/discovery tools, verified real ---
    "Jira Product Discovery", "NotebookLM", "Dovetail", "Maze",
    # --- "MS Project" abbreviation, checked and confirmed NOT redundant
    # with O*NET's "Microsoft Project" entry - 305 hits for the
    # abbreviation vs 228 for the full name, largely non-overlapping
    # postings, so this is real additive signal O*NET's exact-name entry
    # would miss ---
    "MS Project",
    # --- round 2: PM frameworks/methodologies and program names, all
    # checked against the real dataset first. Kanban (224) is a
    # substantial real signal. "CSPO" deliberately NOT re-added here -
    # already exists in CERTIFICATIONS, would duplicate. "RICE" alone
    # NOT added (separately from "RICE Scoring") - too short/generic a
    # word to safely match without a collision check, which hasn't been
    # done; "RICE Scoring" is the safer, already-specific phrasing.
    # "Reforge," "Pragmatic Institute," and "Product School" included
    # here as program/company names (not certifications) - consistent
    # with the earlier decision that Reforge specifically is a
    # membership product, not a certification. "LeSS" is in
    # CASE_SENSITIVE_TERMS above - matched against original case only,
    # since lowercase "less" is one of the most common words in English
    # (1,722 false-positive hits found vs 1 real hit case-sensitive). ---
    "Jobs-to-be-Done", "JTBD", "Product-Led Growth", "PLG",
    "RICE Scoring", "Kano Model", "Opportunity Solution Tree",
    "North Star Metric", "Working Backwards", "Shape Up",
    "Kanban", "Scrumban", "LeSS", "Reforge", "Pragmatic Institute",
    "Product School", "PMC-I", "PSPO", "SAFe POPM", "Claude Cowork",
]

# Hand-curated list of modern AI/data-engineering tools that O*NET's
# Software Skills database does NOT yet cover - confirmed by direct
# inspection of real O*NET rows (none of PyTorch, TensorFlow, LangChain,
# Kubernetes, Airflow, dbt, Snowflake, Databricks, vector databases, or
# any LLM-specific terminology appeared anywhere in several hundred real
# rows checked). O*NET updates on a slower government review cycle and
# simply hasn't incorporated the 2023-2026 AI/data-engineering wave yet.
# This list exists to fill exactly that gap, the same trust model as
# CERTIFICATIONS/CLEARANCES above: a small, maintained, real list beats
# either (a) pretending O*NET is complete, or (b) pulling from an
# unverified Kaggle/scraped dataset (checked and rejected - see project
# notes; most candidate Kaggle tech-skills datasets were either synthetic
# (built with Python Faker), scraped from LinkedIn/Indeed with the same
# ToS exposure as Credly, or generic non-aerospace tech roles with no
# real provenance). Reported in its own labeled section, separate from
# the O*NET-sourced Tools & Software section, so the two sourcing
# methods are never conflated. Every term is included regardless of hit
# count in this specific aerospace dataset - see PM_DESIGN_TOOLS comment
# above for why exclusion-by-zero-count was the wrong call.
AI_DATA_TOOLS = [
    "PyTorch", "TensorFlow", "LangChain", "Hugging Face", "Airflow",
    "dbt", "Snowflake", "Databricks", "Kubernetes", "Docker",
    "Pinecone", "Weaviate", "vector database", "RAG", "fine-tuning",
    "LLM", "Anthropic", "Claude", "OpenAI", "GPT", "Vertex AI",
    "Amazon Bedrock", "Model Context Protocol", "MCP",
    # --- round 2 additions: data-engineering/lakehouse/vector-search
    # stack, each checked against the real dataset before inclusion
    # (Elasticsearch=171, Apache Spark=41, Apache Kafka=41 are the
    # largest real signals found in this round) ---
    "Elasticsearch", "Apache Spark", "Apache Iceberg", "Delta Lake",
    "Apache Hudi", "LlamaIndex", "MLflow", "Apache Kafka", "BigQuery",
    "Dataflow", "Dagster", "Prefect", "Qdrant", "Chroma", "pgvector",
    "Weights & Biases", "Great Expectations", "Apache Polaris",
    "Unity Catalog",
    # --- round 3: lakehouse file formats/platforms and AI orchestration
    # tools, each checked against the real dataset before inclusion.
    # "ORC" deliberately excluded - checked context, the one hit found
    # was "Operator in Responsible Charge" (a regulatory term), not the
    # Apache file format - same false-positive pattern as Linear/CPM.
    # "Mage" deliberately deferred, not excluded - 0 hits found, and it's
    # short/generic enough that it needs a collision check before being
    # trusted with simple word-boundary matching, which hasn't been done
    # yet. "AWS SageMaker" written as bare "SageMaker" below - checked
    # both forms, the bare form returned 46 hits vs 11 for the prefixed
    # form, meaning most real postings drop the "AWS" prefix.
    "Microsoft Fabric", "Parquet", "Avro", "Milvus", "SQLMesh",
    "LangGraph", "Hugging Face Transformers", "SageMaker",
    # --- round 4: Elasticsearch sub-components, missed in the original
    # Elasticsearch addition (round 2) when only the headline term was
    # checked, not its actual stack components from the same source
    # document. "ELK" checked for collision risk (3-letter term, same
    # risk class as ISS/CPM/ORC) and confirmed clean - every sample found
    # was a genuine "ELK Stack (Elasticsearch, Logstash, Kibana)"
    # reference. ELK (128) and Kibana (86) are larger real signals than
    # several entries already in this list. ---
    "ELK", "Kibana", "Logstash", "Elastic Stack", "Filebeat", "Lucene",
    "Hybrid Search", "BM25", "Dense Vector Fields", "ELSER", "Knn Search",
    # --- round 5: agentic AI terms, checked against the real dataset
    # first. "agentic ai" (121) and "multi-agent" (78) are substantial
    # real signals. "MCP" and "Model Context Protocol" already existed
    # in this list (round 1) - not duplicated here. "AWS AI Agents" and
    # "Bedrock Agents" returned 0 hits - kept anyway per the standing
    # zero-count-inclusion rule, both are real, named Amazon products. ---
    "agentic ai", "multi-agent", "agent orchestration", "tool calling",
    "function calling", "AWS AI Agents", "Bedrock Agents",
]

# Terms checked and found to be FALSE POSITIVES - not added to any list,
# documented here so they aren't accidentally re-introduced later.
# "Linear" (the PM/issue-tracking tool) returned 340 hits dataset-wide,
# but checking the actual context of every sample found 100% were the
# common mathematical/engineering adjective ("linear regression,"
# "linear and nonlinear FEA," "linear optimization problems") - zero
# relation to the tool. Same failure mode as the SAFe/safe and sensor
# fusion findings above: a real product name that collides with an
# extremely common English/technical word cannot be safely matched with
# simple word-boundary search the way distinctive brand names can.
FALSE_POSITIVE_TERMS_EXCLUDED = {
    "Linear": "the PM tool name collides with the common math/engineering "
              "adjective 'linear' - checked real samples, 340/340 hits were "
              "'linear regression,' 'linear FEA,' etc, none were the tool.",
    "DER": "Designated Engineering Representative (a real, well-known "
           "aerospace credential) is unverifiable via simple word-boundary "
           "matching in this dataset - 358 raw hits checked, found to be "
           "dominated by the German word 'der' ('the'), since this dataset "
           "includes German-language Airbus postings (Donauwörth, Germany "
           "site). A genuinely different contamination class than prior "
           "findings (not an English-word collision, a foreign-language "
           "one) - simple matching cannot separate the real signal from "
           "the noise without language-detection logic that doesn't exist "
           "yet. Do not re-add without building that first.",
    "DAR": "Design Approval Representative / Designated Airworthiness "
           "Representative - same German-language contamination as DER "
           "above, plus collision with unrelated acronyms (DTAES, military "
           "unit designators). 49 raw hits checked, real signal "
           "unverifiable with current matching logic.",
}

# Terms known to be contaminated by single-company boilerplate in THIS
# dataset, kept here as documentation rather than silently dropped, so
# the finding isn't lost and isn't accidentally re-introduced by a
# future list addition. "sensor fusion" returned 2,157 hits dataset-wide
# - but 2,089 of those (97%) are Anduril Industries' identical "About the
# company" paragraph ("Anduril is committed to bringing cutting-edge
# autonomy, AI, computer vision, sensor fusion, and networking technology
# to the military...") pasted verbatim into every posting regardless of
# role - confirmed by checking that 100% of Anduril's 2,089 postings
# contain the exact same surrounding sentence. With Anduril excluded, the
# real signal is 68 jobs - small, genuine, concentrated in actual
# engineering titles (Lockheed Martin, Joby Aviation, etc). This is the
# real number; the 2,157 headline figure is not usable.
BOILERPLATE_CONTAMINATED_TERMS = {
    "sensor fusion": "97% of hits are Anduril Industries boilerplate company description, not per-role skill signal. Real cross-company count (Anduril excluded): 68 jobs.",
}

# Autonomous Vehicle / ADAS crossover terms - checking whether aerospace
# companies are hiring for automotive-autonomy-adjacent skillsets (e.g.
# eVTOL/drone autonomy programs borrowing automotive safety standards or
# robotics middleware). Each term verified real via direct source check
# (ROS Answers, dSPACE/PatSnap/arxiv technical sources on ISO 26262/ASIL/
# SOTIF) and confirmed present in this dataset before inclusion - though
# per the project's standing rule (see PM_DESIGN_TOOLS), absence in this
# dataset would not be a reason to exclude a verified-real term either.
# "sensor fusion" deliberately excluded - see BOILERPLATE_CONTAMINATED_
# TERMS above. ISO 26262/ASIL are road-vehicle standards; their presence
# in aerospace postings is itself notable since they aren't written for
# aircraft (DO-178C is the aerospace-native equivalent) - a posting using
# both signals a company explicitly borrowing automotive safety practice
# for an autonomy program, which is the actual crossover signal this list
# exists to detect.
AV_AEROSPACE_CROSSOVER = [
    "ISO 26262", "ASIL", "ISO 21448", "SOTIF", "ISO 21434", "ASPICE",
    "ROS", "ROS2", "CARLA", "Gazebo", "NVIDIA Omniverse", "CarMaker",
    "dSPACE", "NVIDIA DRIVE", "CAN bus", "TARA", "ODD",
    "Vector CANoe", "CANalyzer", "Foxglove",
]

# Aerospace-native MBSE/CFD/synthetic-reality engineering platforms - the
# actual aerospace counterparts to consumer/automotive AI tooling (e.g.
# Ansys SCADE is the DO-178C-certifiable equivalent of what NVIDIA DRIVE
# does for automotive). Each checked against the real dataset before
# inclusion. CATIA (474) and Teamcenter (418) are enormous, previously
# unreported signals - bigger than nearly everything else in any
# hand-curated list in this project. Both added here as a safety net
# even though some O*NET coverage may exist for similar tools (e.g.
# Siemens NX is a confirmed real O*NET row) - the two mechanisms use
# different matching logic (exact vs fuzzy partial), so any overlap is
# harmless duplication, not a double-count risk, the way two entries in
# the SAME list would be. "Nucleus" deliberately excluded - checked
# context, the one hit found was the ordinary English word ("the
# nucleus for ADS Airframe Engineering"), not Aechelon's product.
# AVxCELERATE, Aerospace Blockset, SIMULIA returned 0 hits - kept anyway
# per the standing zero-count-inclusion rule.
AEROSPACE_NATIVE_PLATFORMS = [
    "SCADE", "AVxCELERATE", "Aerospace Blockset", "3DEXPERIENCE",
    "SIMULIA", "Teamcenter", "Xcelerator", "CATIA",
]

# AI training/inference hardware and infrastructure - GPU compute and
# secure-cloud terms, a genuinely different category from software
# tools (this describes physical compute and where it's hosted, not a
# framework or library). Checked against the real dataset before
# inclusion. All specific GPU model numbers (H100, A100, MI300X, RTX
# 6000 Ada, GH200, HGX) returned 0 hits - kept anyway per the standing
# rule, and itself a finding worth knowing: aerospace job postings in
# this dataset describe AI compute needs in generic terms ("GPU
# cluster," "high-performance computing") rather than naming specific
# chips, unlike the precise framework/tool naming seen elsewhere
# (PyTorch, Kubernetes, etc). InfiniBand (11) is the only nonzero hit
# found among the hardware-specific terms.
AI_INFRASTRUCTURE_HARDWARE = [
    "H100", "A100", "MI300X", "RTX 6000 Ada", "GH200", "NVLink",
    "InfiniBand", "HGX",
]


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&\w+;", " ", text)
    return text


def _tokenize(text: str) -> list:
    return re.findall(r"[a-z0-9]+", clean_text(text).lower())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) == 0:
        return len(b)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def _tokens_match(title_token: str, term_token: str) -> bool:
    if title_token == term_token:
        return True
    if len(term_token) < 4 or len(title_token) < 4:
        return False
    # Distance capped at 1 regardless of word length. Distance 2 was
    # matching genuinely different words as typos of each other - e.g.
    # "project" vs "product" sits at distance 2, which caused every
    # Project Manager posting to match a "product manager" search. Real
    # typos (enginer/engineer, manger/manager) sit at distance 1.
    #
    # Both sides must clear the length-4 minimum, not just term_token -
    # a 3-letter title token like "ISS" (International Space Station)
    # was fuzzy-matching 4-letter terms like "isso"/"issm"/"isse" at
    # distance 1, causing an unrelated RF Communications title to match
    # three different cybersecurity acronym searches. Short tokens on
    # either side now require an exact match, no fuzzy tolerance.
    return _levenshtein(title_token, term_token) <= 1


def title_matches_term(title: str, term: str) -> bool:
    """ALL core tokens of `term` must fuzzy-match somewhere in `title`.
    Used for job-function matching, where precision matters more than
    recall - we want "program manager" to match titles, not just any
    title containing the word "manager"."""
    title_tokens = _tokenize(title)
    term_tokens = _tokenize(term)
    if not term_tokens:
        return False
    for term_tok in term_tokens:
        if not any(_tokens_match(tt, term_tok) for tt in title_tokens):
            return False
    return True


def _distinctive_tokens_from_original(name: str) -> list:
    """Identifies which tokens in a tool name are actually distinctive
    (acronym or brand-like), using the ORIGINAL case of the name rather
    than the lowercased version. This replaces an earlier stopword-list
    approach (COMMON_WORDS_NOT_SIGNAL) that tried to blacklist ordinary
    English words one at a time - that approach is fundamentally
    unwinnable against O*NET's verbose descriptive naming convention,
    where obscure tools have names like "Reactor excursion and release
    analysis program RELAP". No finite exclusion list covers every
    possible descriptive word O*NET might use, and testing confirmed
    these names were matching 95%+ of jobs because common words like
    "analysis" and "program" were never explicitly excluded.

    The actual reliable signal: a token is distinctive if, in the
    ORIGINAL (non-lowercased) name, it is either (a) short and entirely
    uppercase - an acronym like RELAP, SAP, JIRA, SPICE - or (b) a
    capitalized word that is NOT a common English/business word (e.g.
    "Adobe", "Atlassian", "MathWorks" pass; "Analysis", "Program",
    "System" fail even though they're capitalized, because they're
    ordinary words that happen to start a sentence/title-case phrase).
    """
    raw_words = re.findall(r"[A-Za-z0-9]+", name)
    distinctive = []
    for w in raw_words:
        lw = w.lower()
        if lw in GENERIC_TOOL_WORDS or lw in ENGLISH_STOPWORDS or lw in JOB_POSTING_STOPWORDS:
            continue
        # All-caps acronym (2-6 letters) - RELAP, SAP, JIRA, SPICE, NX
        if w.isupper() and 2 <= len(w) <= 8:
            distinctive.append(lw)
            continue
        # Capitalized word that isn't a generic English business word -
        # treat as a likely brand name (Adobe, Atlassian, MathWorks,
        # Autodesk). Reject ordinary capitalized words from title-case
        # descriptive phrases (Analysis, Program, Reactor, Release).
        if w[0].isupper() and lw not in ORDINARY_CAPITALIZED_WORDS:
            distinctive.append(lw)
    return distinctive


# Ordinary words that frequently appear capitalized in O*NET's
# descriptive/title-case tool names but are NOT brand-distinctive on
# their own - without this, "Analysis", "Program", "System", "Reactor"
# etc. would be wrongly treated as brand names just for being capitalized
# at the start of a title-case phrase.
ORDINARY_CAPITALIZED_WORDS = {
    "analysis", "program", "system", "systems", "software", "application",
    "applications", "management", "reporting", "report", "tracking",
    "tracker", "design", "development", "planning", "scheduling",
    "database", "network", "security", "service", "services", "data",
    "process", "processing", "control", "controls", "model", "modeling",
    "simulation", "simulator", "reactor", "release", "excursion",
    "emergency", "response", "operations", "operation", "record",
    "records", "information", "communication", "communications",
    "education", "training", "consortium", "research", "institute",
    "national", "international", "center", "centers", "agency",
    "organization", "department", "division", "bureau", "office",
    "administration", "standard", "standards", "assessment", "display",
    "monitoring", "evaluation", "documentation", "library", "resource",
    "resources", "toolbox", "tool", "tools", "framework", "suite",
    "package", "platform", "solution", "solutions", "technology",
    "technologies", "engineering", "manufacturing", "construction",
    "accounting", "financial", "medical", "health", "environment",
    "environmental", "quality", "safety", "compliance", "regulatory",
    "consortium", "interuniversity", "occupational", "conservation",
    "atlas", "wind", "circuit", "integrated", "hierarchical",
    "emphasis", "mapping", "disease", "with", "and", "for", "of", "the",
    "project", "manager", "manage", "managing", "team", "office",
    "access", "exchange", "word", "excel", "outlook", "publisher",
    "laboratory", "laboratories", "university", "college", "academy",
    "foundation", "society", "association", "federation", "union",
    # --- round 3: generic business/marketing words found responsible for
    # Red Hat Enterprise Linux, Marketo Marketing Automation, and
    # Microsoft Active Directory matching far too broadly (these words
    # are common in unrelated job-posting contexts: "enterprise systems,"
    # "active management," "cloud computing experience," "marketing
    # principles" all appear constantly without meaning the specific tool) ---
    "enterprise", "marketing", "automation", "active", "directory",
    "cloud", "edition", "creative", "rights", "advanced", "professional",
    "premium", "standard", "basic", "essential", "essentials", "complete",
    "ultimate", "pro", "plus", "global", "digital", "next", "generation",
    # --- round 4: "integration" (missed word-form variant of the
    # already-excluded "integrated" - found responsible for "Microsoft
    # SQL Server Integration Services SSIS" matching 800/1283 systems
    # engineering postings via "systems integration," "integration
    # testing," etc, with the actual SSIS product having zero real
    # mentions). "after" and other common prepositions/conjunctions
    # (found responsible for "Adobe After Effects" matching 92/92 - 100%
    # - of one company's systems-engineering postings via ordinary
    # phrases like "after completion of training," with zero Adobe
    # relation - "after" being missed suggests this whole word category
    # was never systematically covered, so the related prepositions
    # below were added proactively rather than waiting to find each one
    # broken individually) ---
    "integration", "after", "before", "during", "while", "since", "until",
    "within", "without", "between", "among", "through", "throughout",
}


def _build_phrase_pattern(name_tokens: list):
    """Compiles the adjacent-phrase regex once per tool name, not once
    per job. Returns a compiled pattern object."""
    pattern = r"\b" + r"(?:\W+\w+){0,2}\W+".join(re.escape(t) for t in name_tokens) + r"\b"
    return re.compile(pattern)


def match_strength_precomputed(text_lower: str, text_tokens: set,
                                name_tokens: list, compiled_phrase,
                                distinctive_tokens: list) -> float:
    """Same two-tier logic as before, but takes pre-tokenized/pre-lowered
    job text, a pre-compiled phrase pattern, and pre-computed distinctive
    tokens (see _distinctive_tokens_from_original), so all expensive/
    one-time work happens upstream in onet_tool_lookup.

    Tier 1 (1.0): full phrase found, word order respected.
    Tier 2 (0.5): at least one genuinely distinctive token (acronym or
    real brand word, NOT just any non-stopword) is present. distinctive_
    tokens is now computed from the tool's ORIGINAL capitalization, not
    from a stopword blacklist - see _distinctive_tokens_from_original
    for why the blacklist approach failed on O*NET's verbose names."""
    if compiled_phrase.search(text_lower):
        return 1.0
    if not distinctive_tokens:
        return 0.0
    for t in distinctive_tokens:
        if t in text_tokens:
            return 0.5
    return 0.0


def partial_match_strength(full_text: str, name: str) -> tuple:
    """Single-pair convenience wrapper (used by tests / one-off checks).
    For bulk lookups across many jobs x many tools, use
    match_strength_precomputed via onet_tool_lookup instead - this
    version recomputes tokenization, regex compilation, and distinctive-
    token detection every call, fine for one pair but wasteful at scale."""
    name_tokens = [t for t in _tokenize(name) if t not in GENERIC_TOOL_WORDS]
    if not name_tokens:
        return 0.0, []
    text_lower = full_text.lower()
    compiled = _build_phrase_pattern(name_tokens)
    if compiled.search(text_lower):
        return 1.0, name_tokens
    distinctive = _distinctive_tokens_from_original(name)
    if not distinctive:
        return 0.0, []
    text_tokens = set(_tokenize(full_text))
    matched = [t for t in distinctive if t in text_tokens]
    if matched:
        return 0.5, matched
    return 0.0, []


def load_term_file(path: Path) -> list:
    terms = []
    if not path.exists():
        return terms
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line.lower())
    return terms


def load_job_functions(job_functions_dir: Path) -> dict:
    functions = {}
    if not job_functions_dir.exists():
        return functions
    for f in sorted(job_functions_dir.glob("*.txt")):
        terms = load_term_file(f)
        if terms:
            functions[f.stem] = terms
    return functions


def load_onet_tools(path: Path) -> list:
    """Loads the O*NET Software Skills file (tab-delimited). Returns a
    deduplicated list of distinct "Workplace Example" tool/software names
    (the proper-noun column), regardless of which O*NET occupation they're
    tied to - function-bucket scoping already filters the job postings,
    so filtering the tool list by occupation code would be redundant and
    risks excluding a tool just because O*NET happened to tag it under an
    occupation outside your function bucket's exact title match."""
    if not path.exists():
        return []
    names = set()
    skipped_not_hot = 0
    with path.open(encoding="utf-8") as f:
        header = f.readline()
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5 or not parts[1].strip():
                continue
            workplace_example = parts[1].strip()
            hot_technology = parts[4].strip()
            if hot_technology == "Y":
                names.add(workplace_example)
            else:
                skipped_not_hot += 1
    if skipped_not_hot:
        print(f"  (filtered to Hot Technology=Y only - excluded {skipped_not_hot:,} non-hot rows, "
              f"including most generic category names like 'Database reporting software')")
    return sorted(names)


def count_keyword_per_job(corpus_lower: pd.Series, term: str) -> int:
    escaped = re.escape(term.lower())
    pattern = r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"
    return int(corpus_lower.str.contains(pattern, regex=True, na=False).sum())


def named_list_lookup(matched: pd.DataFrame, cleaned_corpus: pd.Series,
                       names: list, top_n_companies: int = 3) -> list:
    """For a small maintained list (certifications, clearances): exact
    word-boundary phrase match per job, with top companies per hit.
    Returns list of dicts sorted by count descending, including zero-count
    entries (a real "not found" result is informative).

    Terms in CASE_SENSITIVE_TERMS (e.g. "SAFe") are matched against the
    ORIGINAL-CASE corpus instead of the lowercased one - found necessary
    because "SAFe" (Scaled Agile Framework) collides with the ordinary
    word "safe" under case-insensitive matching, inflating the real count
    (471) by 20x (9,191) by also catching "safe operations," "safety-
    critical," etc. Verified this is specific to SAFe - other short
    acronyms checked at the same time did not show the same collision."""
    results = []
    corpus_lower = cleaned_corpus.str.lower()
    for name in names:
        if name in CASE_SENSITIVE_TERMS:
            pattern = r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])"
            mask = cleaned_corpus.str.contains(pattern, regex=True, na=False)
        else:
            pattern = r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])"
            mask = corpus_lower.str.contains(pattern, regex=True, na=False)
        count = int(mask.sum())
        companies = Counter(matched.loc[mask, "company"]).most_common(top_n_companies) if count else []
        results.append({"name": name, "count": count, "companies": companies})
    results.sort(key=lambda r: -r["count"])
    return results


def _build_tool_index(onet_names: list) -> dict:
    """Inverted index: distinctive_token -> set of tool names. Lets us
    find which tools are even POSSIBLE candidates for a job by looking
    up the job's own tokens against this index (cheap dict lookups),
    instead of testing every one of 8,753 tool patterns against every
    job's raw text.

    Indexed by DISTINCTIVE tokens only (see
    _distinctive_tokens_from_original) - not every non-generic token.
    Earlier versions indexed by all non-generic tokens, which meant
    ordinary descriptive words inside long O*NET names (e.g. "analysis",
    "program", "reactor" in "Reactor excursion and release analysis
    program RELAP") were treated as index keys and, combined with a
    flawed distinctiveness check downstream, caused those obscure tools
    to match 95%+ of jobs. Indexing by distinctive tokens only means a
    tool like RELAP has exactly one index entry ("relap"), so it only
    becomes a candidate for jobs that actually mention "relap" - which
    in an aerospace PM dataset is correctly almost never.

    This is also the fix for the multi-minute runtime: testing showed
    that running thousands of compiled regex patterns against every
    job's raw text was O(tools x text_length); the index inverts this so
    only realistic candidates (tools whose distinctive token appears in
    that job) are ever checked."""
    index = defaultdict(set)
    name_token_map = {}
    name_distinctive_map = {}
    for name in onet_names:
        name_tokens = tuple(t for t in _tokenize(name) if t not in GENERIC_TOOL_WORDS)
        if not name_tokens:
            continue
        distinctive = _distinctive_tokens_from_original(name)
        if not distinctive:
            continue  # no real signal in this name at all - skip entirely
        name_token_map[name] = name_tokens
        name_distinctive_map[name] = distinctive
        for tok in set(distinctive):
            index[tok].add(name)
    return index, name_token_map, name_distinctive_map


def onet_tool_lookup(matched: pd.DataFrame, cleaned_corpus: pd.Series,
                      onet_names: list, top_n_companies: int = 3,
                      min_strength: float = 0.5) -> list:
    """For each O*NET tool name, computes per-job match strength (1.0 =
    exact adjacent phrase, 0.5 = a distinctive non-generic token present,
    0.0 = no match - see match_strength_precomputed), keeps jobs at or
    above min_strength, reports count/percent/avg strength/top companies.

    Uses an inverted token index (_build_tool_index) so only tools whose
    tokens actually appear in a given job are ever checked against that
    job - see _build_tool_index's docstring for why this was necessary
    (brute-force regex-per-tool was ~24 minutes at full O*NET scale;
    indexed approach is ~1 second).
    """
    texts = cleaned_corpus.fillna("").tolist()
    job_data = []
    for text in texts:
        if not text:
            job_data.append(None)
            continue
        job_data.append((text.lower(), set(_tokenize(text))))

    tool_index, name_token_map, name_distinctive_map = _build_tool_index(onet_names)
    phrase_cache = {}  # compiled regex per name, built lazily, reused across jobs

    name_hits = defaultdict(list)  # name -> list of (job_idx, strength)

    for idx, jd in enumerate(job_data):
        if jd is None:
            continue
        text_lower, text_tokens = jd

        candidates = set()
        for tok in text_tokens:
            if tok in tool_index:
                candidates.update(tool_index[tok])

        for name in candidates:
            name_tokens = name_token_map[name]
            distinctive = name_distinctive_map[name]
            if name not in phrase_cache:
                phrase_cache[name] = _build_phrase_pattern(list(name_tokens))
            compiled_phrase = phrase_cache[name]
            strength = match_strength_precomputed(text_lower, text_tokens, list(name_tokens),
                                                   compiled_phrase, distinctive)
            if strength >= min_strength:
                name_hits[name].append((idx, strength))

    results = []
    for name, hits in name_hits.items():
        hit_indices = [h[0] for h in hits]
        strengths = [h[1] for h in hits]
        companies = Counter(matched.iloc[hit_indices]["company"]).most_common(top_n_companies)
        results.append({
            "name": name,
            "count": len(hit_indices),
            "avg_strength": sum(strengths) / len(strengths),
            "companies": companies,
        })

    results.sort(key=lambda r: -r["count"])
    return results


def analyze_function(df: pd.DataFrame, function_name: str, title_terms: list,
                      user_keywords: list, onet_names: list) -> dict:
    total_dataset_jobs = len(df)

    mask = df["title"].fillna("").apply(
        lambda title: any(title_matches_term(title, term) for term in title_terms)
    )
    matched = df[mask].copy()

    result = {
        "function": function_name,
        "total_jobs": len(matched),
        "total_dataset_jobs": total_dataset_jobs,
        "seniority": Counter(),
        "user_keyword_hits": {},
        "certifications": [],
        "aerospace_certifications": [],
        "clearances": [],
        "ai_data_tools": [],
        "pm_design_tools": [],
        "av_aerospace_crossover": [],
        "aerospace_native_platforms": [],
        "ai_infrastructure_hardware": [],
        "tools": [],
        "companies": Counter(),
        "salary_by_seniority": {},
        "non_us_excluded_from_salary": 0,
    }

    if len(matched) == 0:
        return result

    seniority_norm = matched["seniority"].fillna("Unknown").str.strip().str.title()
    result["seniority"] = Counter(seniority_norm)
    result["companies"] = Counter(matched["company"])

    # --- Salary: US-only, USD. Two US labels exist in this dataset
    # ("United States of America" and "US") - both included. Non-US
    # postings are excluded from the math, not silently averaged in,
    # and the excluded count is reported. ---
    matched["_seniority_norm"] = seniority_norm
    matched["_parsed_salary"] = matched["salary"].apply(parse_salary_to_annual)
    is_us = matched["country"].isin(US_COUNTRY_LABELS)
    result["non_us_excluded_from_salary"] = int((~is_us & matched["_parsed_salary"].notna()).sum())
    us_matched = matched[is_us]

    salary_by_seniority = {}
    for level in us_matched["_seniority_norm"].unique():
        level_jobs = us_matched[us_matched["_seniority_norm"] == level]
        with_salary = level_jobs["_parsed_salary"].dropna()
        if len(with_salary) > 0:
            salary_by_seniority[level] = {
                "avg": float(with_salary.mean()),
                "median": float(with_salary.median()),
                "n_with_salary": int(len(with_salary)),
                "n_total": int(len(level_jobs)),
            }
    result["salary_by_seniority"] = salary_by_seniority

    cleaned_desc = matched["description_text"].fillna("").apply(clean_text)
    cleaned_title = matched["title"].fillna("").apply(clean_text)
    full_corpus = cleaned_desc + " " + cleaned_title

    # --- User ad hoc keyword check ---
    if user_keywords:
        corpus_lower = full_corpus.str.lower()
        hits = {}
        for term in user_keywords:
            count = count_keyword_per_job(corpus_lower, term)
            if count > 0:
                hits[term] = count
        result["user_keyword_hits"] = dict(sorted(hits.items(), key=lambda x: -x[1]))

    # --- Certifications & clearances: small maintained lists, exact
    # word-boundary match, zero-count entries kept (a real "not found"
    # result is informative). ---
    result["certifications"] = named_list_lookup(matched, full_corpus, CERTIFICATIONS)
    result["aerospace_certifications"] = named_list_lookup(matched, full_corpus, AEROSPACE_CERTIFICATIONS)
    result["clearances"] = named_list_lookup(matched, full_corpus, CLEARANCES)

    # --- Modern AI/data-engineering tools, and PM/design tools: separate
    # hand-curated lists, exact match (same mechanism as certifications/
    # clearances, not the fuzzy O*NET partial-match logic - these lists
    # are small and already verified, so exact matching is sufficient and
    # keeps their sourcing distinct from the O*NET-derived Tools &
    # Software section below). ---
    result["ai_data_tools"] = named_list_lookup(matched, full_corpus, AI_DATA_TOOLS)
    result["pm_design_tools"] = named_list_lookup(matched, full_corpus, PM_DESIGN_TOOLS)
    result["av_aerospace_crossover"] = named_list_lookup(matched, full_corpus, AV_AEROSPACE_CROSSOVER)
    result["aerospace_native_platforms"] = named_list_lookup(matched, full_corpus, AEROSPACE_NATIVE_PLATFORMS)
    result["ai_infrastructure_hardware"] = named_list_lookup(matched, full_corpus, AI_INFRASTRUCTURE_HARDWARE)

    # --- Tools & software: O*NET reference list, fuzzy partial match. ---
    if onet_names:
        result["tools"] = onet_tool_lookup(matched, full_corpus, onet_names)

    return result


# ---------------------------------------------------------------------------
# Salary parsing (unchanged from prior version)
# ---------------------------------------------------------------------------

def parse_salary_to_annual(raw) -> float:
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None

    is_hourly = bool(re.search(r'/\s*(hr|hour)', raw, re.IGNORECASE))

    segments = [s.strip() for s in raw.split(';') if s.strip()]
    if not segments:
        return None

    midpoints = []
    for seg in segments:
        numbers = re.findall(r'[\d,]+\.?\d*', seg)
        nums = []
        for n in numbers:
            cleaned = n.replace(',', '')
            try:
                nums.append(float(cleaned))
            except ValueError:
                continue
        if not nums:
            continue
        midpoint = (nums[0] + nums[1]) / 2 if len(nums) >= 2 else nums[0]
        midpoints.append(midpoint)

    if not midpoints:
        return None

    avg_midpoint = sum(midpoints) / len(midpoints)

    if is_hourly or avg_midpoint < 100:
        avg_midpoint = avg_midpoint * 2080

    return avg_midpoint


def print_function_report(result: dict) -> None:
    total = result["total_jobs"]
    total_dataset = result["total_dataset_jobs"]
    pct_of_dataset = (total / total_dataset * 100) if total_dataset else 0

    print(f"\n{'='*70}")
    print(f"JOB FUNCTION: {result['function']}")
    print(f"{'='*70}")
    print(f"Jobs matched: {total:,} / {total_dataset:,} total in dataset ({pct_of_dataset:.1f}%)")

    if total == 0:
        print("  No jobs matched this function's title terms.")
        return

    print(f"\n{'-'*70}")
    print(f"SENIORITY BREAKDOWN (% of the {total:,} jobs matched in this function)")
    print("Bar scale: each # = 3 percentage points")
    print(f"{'-'*70}")
    for level, count in result["seniority"].most_common():
        pct = count / total * 100
        bar = "#" * min(int(pct / 3), 33)
        print(f"  {level:<15} {count:>5}  {bar} {pct:.1f}%")

    if result["salary_by_seniority"]:
        excluded = result["non_us_excluded_from_salary"]
        print(f"\n{'-'*70}")
        print("AVG SALARY BY SENIORITY - US POSTINGS ONLY, IN USD")
        print("Annualized; hourly rates converted at 2,080 hrs/year.")
        print("Only jobs with parseable salary data - coverage shown per level.")
        if excluded:
            print(f"({excluded} non-US postings with salary data excluded from this section)")
        print(f"{'-'*70}")
        ordered_levels = [l for l in SENIORITY_ORDER if l in result["salary_by_seniority"]]
        remaining = [l for l in result["salary_by_seniority"] if l not in ordered_levels]
        for level in ordered_levels + sorted(remaining):
            stats = result["salary_by_seniority"][level]
            coverage_pct = stats["n_with_salary"] / stats["n_total"] * 100
            print(f"  {level:<15} avg ${stats['avg']:>10,.0f}   median ${stats['median']:>10,.0f}   "
                  f"({stats['n_with_salary']}/{stats['n_total']} jobs, {coverage_pct:.0f}% had salary data)")
    else:
        print(f"\n{'-'*70}")
        print("AVG SALARY BY SENIORITY - no US postings with parseable salary data in this function.")
        print(f"{'-'*70}")

    print(f"\n{'-'*70}")
    print("TOP COMPANIES HIRING FOR THIS FUNCTION")
    print(f"{'-'*70}")
    for company, count in result["companies"].most_common(10):
        print(f"  {company:<40} {count:>5}")

    if result["user_keyword_hits"]:
        print(f"\n{'-'*70}")
        print("USER KEYWORD CHECK (your own search terms, this run only)")
        print("Counted per job - a job mentioning a term 5x still counts once.")
        print(f"{'-'*70}")
        for term, count in result["user_keyword_hits"].items():
            pct = count / total * 100
            bar = "#" * min(int(pct / 3), 33)
            print(f"    {term:<35} {count:>5}  {bar} {pct:.1f}%")

    def print_named_list_section(title, subtitle, items):
        print(f"\n{'-'*70}")
        print(title)
        print(subtitle)
        print(f"{'-'*70}")
        if not items:
            print("    (no reference list loaded)")
            return
        for item in items:
            pct = item["count"] / total * 100 if total else 0
            bar = "#" * min(int(pct / 3), 33)
            companies_str = ", ".join(f"{c}: {n}" for c, n in item["companies"]) if item["companies"] else "not found"
            print(f"    {item['name']:<28} {item['count']:>5}  {bar} {pct:.1f}%   [{companies_str}]")

    print_named_list_section(
        "CERTIFICATIONS",
        "Counted per job. Zero-count entries kept - absence is also a finding.",
        result["certifications"],
    )

    print_named_list_section(
        "AEROSPACE COMPLIANCE AND CERTIFICATIONS",
        "INCOSE, AS9100, GD&T, ITAR, FedRAMP, selected AI-governance "
        "standards (EASA AI Roadmap, ARP6983, etc) - not covered by the "
        "general certifications list above. Counted per job, zero-count kept.",
        result["aerospace_certifications"],
    )

    print_named_list_section(
        "SECURITY CLEARANCES",
        "Counted per job. Zero-count entries kept - absence is also a finding.",
        result["clearances"],
    )

    print_named_list_section(
        "AI / DATA ENGINEERING TOOLS (hand-curated list, not from O*NET)",
        "Modern AI/data tools O*NET's slower update cycle doesn't yet cover "
        "(PyTorch, Kubernetes, LangChain, etc). Exact match, zero-count kept.",
        result["ai_data_tools"],
    )

    print_named_list_section(
        "PM / DESIGN / COLLABORATION TOOLS, FRAMEWORKS AND CERTIFICATIONS",
        "Tools (Figma, Smartsheet), frameworks (JTBD, RICE, Kanban), and "
        "program names (Reforge, Pragmatic Institute) missing from O*NET "
        "coverage. Exact match, zero-count kept.",
        result["pm_design_tools"],
    )

    print_named_list_section(
        "AV / AUTONOMOUS VEHICLE CROSSOVER (automotive standards/tools in aerospace postings)",
        "ISO 26262, ASIL, ROS2, CARLA, etc - signals an aerospace company "
        "borrowing automotive autonomy practice. NOTE: 'sensor fusion' is "
        "deliberately excluded from this list - found to be 97% single-"
        "company boilerplate (Anduril's company-description paragraph "
        "pasted into every posting), not genuine per-role skill signal.",
        result["av_aerospace_crossover"],
    )

    print_named_list_section(
        "AEROSPACE-NATIVE ENGINEERING PLATFORMS (MBSE / CFD / synthetic reality)",
        "CATIA, Teamcenter, SCADE, etc - aerospace's actual counterparts "
        "to consumer/automotive AI tools (e.g. Ansys SCADE is the "
        "DO-178C-certifiable equivalent of NVIDIA DRIVE). Exact match, "
        "zero-count kept.",
        result["aerospace_native_platforms"],
    )

    print_named_list_section(
        "AI INFRASTRUCTURE HARDWARE (GPU compute, not software)",
        "H100, A100, InfiniBand, etc - physical AI training/inference "
        "hardware. All specific GPU model numbers returned 0 hits in "
        "testing - kept anyway, itself a finding (postings describe "
        "compute generically rather than naming chips). Exact match, "
        "zero-count kept.",
        result["ai_infrastructure_hardware"],
    )

    print(f"\n{'-'*70}")
    print("TOOLS & SOFTWARE (source: O*NET Software Skills database, CC BY 4.0)")
    print("Match strength: 1.00 = full tool name found, <1.00 = partial/brand-only.")
    print("Minimum strength shown: 0.50. Top 3 companies per tool.")
    print(f"{'-'*70}")
    if not result["tools"]:
        print("    (no O*NET reference file loaded - see data/reference/onet_software_skills.txt)")
    else:
        for item in result["tools"][:30]:
            pct = item["count"] / total * 100 if total else 0
            bar = "#" * min(int(pct / 3), 33)
            companies_str = ", ".join(f"{c}: {n}" for c, n in item["companies"])
            print(f"    {item['name']:<35} {item['count']:>5}  {bar} {pct:.1f}%  "
                  f"(avg strength {item['avg_strength']:.2f})   [{companies_str}]")


def export_results(results: list, export_path: Path) -> None:
    rows = []
    for r in results:
        for level, stats in r["salary_by_seniority"].items():
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "salary_by_seniority_us_usd", "category": level,
                "term": "avg_annual_salary", "count": round(stats["avg"]),
                "pct_of_function": round(stats["n_with_salary"] / stats["n_total"] * 100, 1),
            })
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "salary_by_seniority_us_usd", "category": level,
                "term": "median_annual_salary", "count": round(stats["median"]),
                "pct_of_function": round(stats["n_with_salary"] / stats["n_total"] * 100, 1),
            })
        for term, count in r["user_keyword_hits"].items():
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "user_keyword", "category": "",
                "term": term, "count": count,
                "pct_of_function": round(count / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["certifications"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "certification", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["aerospace_certifications"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "aerospace_certification", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["clearances"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "clearance", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["ai_data_tools"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "ai_data_tool_hand_curated", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["pm_design_tools"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "pm_design_tool_hand_curated", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["av_aerospace_crossover"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "av_aerospace_crossover_hand_curated", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["aerospace_native_platforms"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "aerospace_native_platform_hand_curated", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["ai_infrastructure_hardware"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "ai_infrastructure_hardware_hand_curated", "category": "",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
        for item in r["tools"]:
            rows.append({
                "function": r["function"], "total_jobs_in_function": r["total_jobs"],
                "source": "tool_software_onet", "category": f"strength_{item['avg_strength']:.2f}",
                "term": item["name"], "count": item["count"],
                "pct_of_function": round(item["count"] / r["total_jobs"] * 100, 1) if r["total_jobs"] else 0,
            })
    out_df = pd.DataFrame(rows)
    out_df.to_csv(export_path, index=False)
    print(f"\nResults exported -> {export_path}")


def prompt_for_job_function(job_functions_dir: Path, existing: dict) -> tuple:
    if existing:
        print("\nSaved job functions you can reuse: " + ", ".join(existing.keys()))
    print("\nEnter the job titles that define the role you're researching.")
    print("Comma-separated, e.g.: program manager, project manager, technical program manager")
    raw = input("Titles: ").strip()
    if not raw:
        return None, []

    terms = [t.strip().lower() for t in raw.split(",") if t.strip()]

    name = input("Save this as a job function for next time? Enter a short name, or leave blank to skip: ").strip()
    if name:
        name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
        job_functions_dir.mkdir(parents=True, exist_ok=True)
        out_path = job_functions_dir / f"{name}.txt"
        out_path.write_text("\n".join(terms) + "\n", encoding="utf-8")
        print(f"Saved -> {out_path}. Next time: --function {name}")
    else:
        name = "custom_" + re.sub(r"[^a-z0-9_]+", "_", terms[0])

    return name, terms


def prompt_for_user_keywords() -> list:
    print("\nWant to check for any specific keywords of your own? (your hypothesis,")
    print("checked fresh this run - separate from certifications/clearances/tools below)")
    raw = input("Keywords, comma-separated, or press enter to skip: ").strip()
    if not raw:
        return []
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def main():
    parser = argparse.ArgumentParser(
        description="Job-function-scoped skill DISCOVERY for job seekers (no pre-built keyword files required)"
    )
    parser.add_argument("--input", type=Path, default=Path("data/master_dataset.csv"))
    parser.add_argument("--job-functions-dir", type=Path, default=Path("job_functions"))
    parser.add_argument("--onet-file", type=Path, default=Path("data/reference/onet_software_skills.txt"))
    parser.add_argument("--function", type=str, default=None,
                        help="Reuse a saved job function (filename stem in job_functions/). Skips the title prompt.")
    parser.add_argument("--label", type=str, default=None,
                        help="Clean display name for the report header (e.g. 'Product Management'). "
                             "Defaults to the --function name if not set.")
    parser.add_argument("--keywords", type=str, default=None,
                        help="Comma-separated ad hoc keywords to check, non-interactively.")
    parser.add_argument("--no-prompt-keywords", action="store_true",
                        help="Skip the ad hoc keyword prompt entirely.")
    parser.add_argument("--export", type=Path, default=None,
                        help="Export full results to CSV")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input file not found: {args.input}")
        return

    print(f"Loading {args.input}...")
    df = pd.read_csv(args.input, low_memory=False)
    print(f"  {len(df):,} jobs loaded")

    onet_names = load_onet_tools(args.onet_file)
    if onet_names:
        print(f"  {len(onet_names):,} distinct tool/software names loaded from {args.onet_file}")
    else:
        print(f"  No O*NET reference file found at {args.onet_file} - Tools & Software section will be skipped.")
        print(f"  To enable it: curl -sL \"https://www.onetcenter.org/dl_files/database/db_30_3_text/Software%20Skills.txt\" -o {args.onet_file}")

    existing_functions = load_job_functions(args.job_functions_dir)

    if args.function and args.function in existing_functions:
        function_key = args.function
        title_terms = existing_functions[args.function]
        print(f"\nUsing saved job function '{function_key}': {', '.join(title_terms)}")
    elif args.function:
        print(f"\nNo saved job function named '{args.function}' found in {args.job_functions_dir}/.")
        function_key, title_terms = prompt_for_job_function(args.job_functions_dir, existing_functions)
    else:
        function_key, title_terms = prompt_for_job_function(args.job_functions_dir, existing_functions)

    if not title_terms:
        print("No job titles entered. Nothing to analyze.")
        return

    display_name = args.label if args.label else function_key

    if args.keywords is not None:
        user_keywords = [t.strip().lower() for t in args.keywords.split(",") if t.strip()]
    elif args.no_prompt_keywords:
        user_keywords = []
    else:
        user_keywords = prompt_for_user_keywords()

    result = analyze_function(df, display_name, title_terms, user_keywords, onet_names)
    print_function_report(result)

    if args.export:
        export_results([result], args.export)


if __name__ == "__main__":
    main()

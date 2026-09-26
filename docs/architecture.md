# REAL ESTATE REVENUE OS

## Final Technical Architecture — v1.0

### 0. Product Definition

المنتج هو **Real Estate Revenue Operating System** لشركات الوساطة والتسويق العقاري والمكاتب العقارية.

النظام لا يُعامل كـCRM يحتوي على AI، بل كمنصة تشغيل متكاملة تغطي:

```text
SUPPLY
→ PROPERTY
→ LISTING
→ ACQUISITION
→ LEAD
→ QUALIFICATION
→ MATCHING
→ SALES
→ VIEWING
→ OFFER
→ NEGOTIATION
→ RESERVATION
→ CONTRACT
→ PAYMENT
→ COMMISSION
→ DEAL
→ REVENUE
```

وتعمل فوق هذا المسار:

```text
AI
Automation
Analytics
Intelligence
Integrations
Security
```

---

# 1. Architecture Principles

هذه القواعد غير قابلة للكسر:

### 1.1 Database is the source of truth

PostgreSQL هو المصدر الحقيقي للحالة التجارية.

الـAI، الـcache، الـvector index، والـfrontend ليست Sources of Truth.

### 1.2 AI proposes; Domain decides

```text
AI
→ understands / recommends / proposes

Domain Service
→ validates business rules

Database Transaction
→ commits state

Event
→ announces what happened
```

الـLLM لا يقرر وحده:

* Availability
* Price
* Permission
* Reservation
* Payment
* Commission
* Tenant access
* Contract state

### 1.3 No direct DB access from AI

الـAI لا يستخدم SQL ولا يعرف schema الداخلية.

الوصول يكون:

```text
Agent
→ Tool Gateway
→ Authorization
→ Domain Service
→ Transaction
→ Result
```

### 1.4 No direct provider logic inside business domains

WhatsApp / Meta / Telephony / Calendar / Payments لا تدخل مباشرة إلى Domain Services.

كل provider يدخل من خلال adapter/provider layer.

### 1.5 Modular Monolith first

النسخة الأولى:

```text
FastAPI
+
PostgreSQL
+
Redis
+
Workers
+
Object Storage
+
Workflow Runtime where needed
```

وليست مجموعة Microservices.

كل Domain له boundary واضح، ويمكن فصله لاحقًا إذا ظهر سبب حقيقي.

### 1.6 Event-driven internally

التغيير التجاري يولد Domain Event.

```text
business mutation
→ transaction
→ outbox
→ event processing
```

### 1.7 Idempotency everywhere it matters

كل عملية قد يعاد إرسالها أو retry يجب أن تكون idempotent.

### 1.8 State machines, not random status strings

كل lifecycle رئيسي له State Machine واضحة.

### 1.9 Tenant isolation everywhere

كل request وكل query وكل mutation وكل retrieval يجب أن تكون محدودة بالـtenant من server side.

### 1.10 External information has provenance

السعر والـavailability والمعلومات الحساسة يجب أن يكون لها:

```text
source
updated_at
effective_at
freshness
verification state
```

---

# 2. Final System Architecture

```text
                                   ┌─────────────────────────────┐
                                   │          CLIENTS            │
                                   │                             │
                                   │ Buyers / Sellers / Owners  │
                                   │ Tenants / Brokers / Users   │
                                   └──────────────┬──────────────┘
                                                  │
                         ┌────────────────────────┼────────────────────────┐
                         ↓                        ↓                        ↓
                    WhatsApp                Instagram/Facebook        Website/Webchat
                    Voice/Phone             Email/SMS                 Forms/Landing
                         └────────────────────────┬────────────────────────┘
                                                  ↓
                                  ┌────────────────────────────┐
                                  │      CHANNEL GATEWAY        │
                                  │                            │
                                  │ Verify / Normalize         │
                                  │ Deduplicate / Media        │
                                  │ Identity Resolution        │
                                  │ Rate Limits / Webhooks     │
                                  └──────────────┬─────────────┘
                                                 ↓
                                  ┌────────────────────────────┐
                                  │      ENGAGEMENT CORE       │
                                  │                            │
                                  │ People / Identities        │
                                  │ Leads / Customers          │
                                  │ Conversations / Messages   │
                                  │ Calls / Activities         │
                                  │ Inbox / Tasks / Assignment │
                                  └──────────────┬─────────────┘
                                                 │
               ┌─────────────────────────────────┼────────────────────────────────┐
               ↓                                 ↓                                ↓
      ┌──────────────────┐             ┌──────────────────┐             ┌──────────────────┐
      │ PROPERTY & SUPPLY│             │ SALES & DEALS    │             │ MARKETING &      │
      │                  │             │                  │             │ ACQUISITION      │
      │ Projects         │             │ Opportunities    │             │ Campaigns        │
      │ Units/Assets     │             │ Matching         │             │ Sources          │
      │ Inventory        │             │ Routing          │             │ Attribution      │
      │ Listings         │             │ Viewings         │             │ Landing Pages    │
      │ Pricing          │             │ Offers           │             │ Distribution     │
      │ Owners           │             │ Negotiation      │             │                  │
      │ Developers       │             │ Reservations     │             │                  │
      │ Mandates         │             │ Contracts        │             │                  │
      │ Supply Sources   │             │ Deals            │             │                  │
      └────────┬─────────┘             │ Finance          │             └────────┬─────────┘
               │                       └────────┬─────────┘                      │
               └───────────────────────┬────────┴────────────────────────────────┘
                                       ↓
                              ┌──────────────────────┐
                              │ AUTOMATION PLATFORM  │
                              │                      │
                              │ Events               │
                              │ Rules                │
                              │ Journeys             │
                              │ Follow-up            │
                              │ SLA                  │
                              │ Tasks                │
                              │ Notifications        │
                              └──────────┬───────────┘
                                         ↓
                              ┌──────────────────────┐
                              │ INTELLIGENCE LAYER   │
                              │                      │
                              │ AI Agents            │
                              │ Voice AI             │
                              │ Scoring              │
                              │ Matching             │
                              │ Next Best Action     │
                              │ AI Insights           │
                              │ Revenue Intelligence │
                              │ Market Intelligence  │
                              └──────────┬───────────┘
                                         ↓
                              ┌──────────────────────┐
                              │ PLATFORM FOUNDATION  │
                              │                      │
                              │ Multi-tenancy        │
                              │ Organization/RBAC    │
                              │ Consent              │
                              │ Search               │
                              │ Knowledge            │
                              │ Files                │
                              │ Integrations         │
                              │ Billing/Usage        │
                              │ Audit                │
                              │ Observability        │
                              └──────────────────────┘
```

---

# 3. Platform Hierarchy

Security and organizational ownership يجب أن تكون صريحة:

```text
TENANT
  │
  └── ORGANIZATION
       │
       ├── BRANCHES
       │    ├── Teams
       │    └── Users
       │
       ├── Departments
       │    ├── Sales
       │    ├── Marketing
       │    ├── Finance
       │    └── Operations
       │
       └── External Relationships
            ├── Developers
            ├── Owners
            ├── Partner Agencies
            └── Brokers
```

### Tenant

حدود:

* Security isolation
* Billing
* Usage
* Data isolation

### Organization

الكيان التجاري داخل الـtenant.

### Branch

فرع جغرافي/تشغيلي.

### Team

مجموعة مستخدمين داخل الفرع أو organization.

### User

موظف أو مستخدم داخلي.

---

# 4. Customer & Identity Domain

الـIdentity system يجب أن يفصل بين الشخص وبين channel identity.

```text
Person
  ├── Phone identity
  ├── WhatsApp identity
  ├── Instagram identity
  ├── Facebook identity
  ├── Email identity
  └── Website identity
```

الهدف:

نفس العميل الذي جاء من Instagram ثم WhatsApp ثم phone لا يصبح 3 Leads منفصلة.

## Core entities

```text
people
identities
organizations
contacts
leads
customer_profiles
customer_preferences
communication_consents
customer_tags
customer_segments
```

---

# 5. Communication Preferences & Consent

هذه Platform/Customer capability وليست Domain منفصل.

لكل Contact:

```text
preferred_channel
language
marketing_consent
transactional_consent
do_not_contact
quiet_hours
channel_opt_ins
channel_opt_outs
```

أي outbound message يمر:

```text
AI / Automation
→ Policy Check
→ Consent / Preference Check
→ Channel Availability
→ Provider
```

---

# 6. Engagement Domain

## Conversations

```text
conversations
conversation_participants
messages
message_attachments
calls
call_transcripts
conversation_assignments
conversation_tags
```

كل Conversation مرتبطة بـ:

* tenant
* person
* channel
* participants
* current owner
* current state

## Message

```text
id
conversation_id
direction
sender
recipient
channel
provider
external_id
message_type
text
attachments
status
created_at
delivered_at
read_at
```

---

# 7. Channel Gateway

كل provider له adapter:

```text
ChannelGateway
├── WhatsAppAdapter
├── InstagramAdapter
├── MessengerAdapter
├── EmailAdapter
├── SMSAdapter
├── WebChatAdapter
└── VoiceAdapter
```

### Inbound flow

```text
Provider
→ webhook
→ signature verification
→ raw event persistence
→ deduplication
→ normalization
→ identity resolution
→ conversation
→ event
```

### Outbound flow

```text
Application/AI
→ message intent
→ policy/consent
→ outbox
→ channel dispatcher
→ provider
→ delivery callback
→ status update
```

---

# 8. Lead Domain

Lead ليس مجرد Contact.

Lead تمثل commercial acquisition/opportunity entry.

## Core fields

```text
lead
├── person_id
├── source
├── campaign_id
├── owner
├── team
├── branch
├── lifecycle_stage
├── qualification_state
├── score
├── intent_score
├── engagement_score
├── fit_score
├── next_action
├── next_action_at
└── created_at
```

---

# 9. Lead State Machine

```text
NEW
 ↓
CONTACTED
 ↓
QUALIFYING
 ↓
QUALIFIED
 ↓
NURTURE
 ↓
CONVERTED
```

Additional terminal/side states:

```text
DISQUALIFIED
DORMANT
LOST
```

لا تجعل الـLLM يغيّر lifecycle مباشرة بدون Domain Service.

---

# 10. Lead Requirements

احتياجات العميل تحفظ بشكل structured:

```text
property_types
areas
min_budget
max_budget
bedrooms
bathrooms
purpose
payment_preference
delivery_preference
finishing_preference
floor_preferences
view_preferences
excluded_features
purchase_timeline
```

### مهم

يفصل النظام بين:

```text
Explicit preference
```

و

```text
Behavioral preference
```

لأن العميل قد يقول شيئًا ثم يتصرف بطريقة مختلفة.

---

# 11. Lead Intelligence

Lead Score يتكون من عدة إشارات:

```text
behavioral signals
+
conversation signals
+
CRM signals
+
business rules
+
optional ML
```

ويكون لدينا على الأقل:

```text
lead_score
intent_score
engagement_score
fit_score
```

الـAI يستخدم النتائج ويفسرها، لكنه ليس المصدر الوحيد لها.

---

# 12. Property & Supply Domain

هذا Domain يجب أن يدعم:

### Primary real estate

```text
Developer
Project
Building
Unit
```

### Secondary market / standalone properties

```text
Owner
Property Asset
Listing
```

لذلك لا نجبر كل العقارات على أن تكون داخل Project/Building.

---

# 13. Canonical Property Model

المفهوم الأساسي:

```text
PropertyAsset
```

وقد يكون:

```text
Standalone Property
```

أو:

```text
Project
  → Building
    → Unit
```

## Core entities

```text
developers
projects
buildings
property_assets
units
owners
supply_sources
mandates
```

---

# 14. Property Asset

```text
property_asset
├── type
├── title
├── location
├── geo
├── bedrooms
├── bathrooms
├── area
├── finishing
├── floor
├── view
├── orientation
├── delivery
├── attributes
├── media
└── source
```

لو كان Unit:

```text
property_asset
→ project
→ building
→ unit
```

---

# 15. Supply Source

كل أصل عقاري يجب أن يعرف مصدره/علاقته التجارية.

```text
Developer
Owner
Partner Agency
Internal Inventory
External Feed
```

مع:

```text
source_id
external_id
source_type
last_synced_at
sync_status
```

---

# 16. Mandates & Rights

هذه capability أساسية للـBrokerage.

```text
MANDATE
├── representation
├── exclusivity
├── listing_rights
├── referral_rights
├── commission_rights
├── start_at
└── expires_at
```

لأن امتلاك العقار وحق تسويقه وحق الحصول على العمولة ليست نفس الشيء.

---

# 17. Inventory Domain

حالة الـUnit ليست Boolean.

## State machine

```text
AVAILABLE
 ↓
HELD
 ↓
RESERVED
 ↓
CONTRACTED
 ↓
SOLD
```

Additional:

```text
BLOCKED
RELEASED
EXPIRED
```

### Hold

كل Hold له:

```text
hold_id
unit_id
created_at
expires_at
created_by
reason
```

---

# 18. Inventory Consistency

الحجز يجب أن يكون Transactional.

```text
request
→ validate
→ authorization
→ check state
→ lock/check concurrency
→ create hold/reservation
→ update inventory
→ commit
→ event
```

لو شخصان حاولا حجز نفس الوحدة في نفس الوقت، يجب أن ينجح واحد فقط.

لا توجد قرارات availability من الـLLM.

---

# 19. Inventory Reconciliation

لو عندنا:

```text
Developer API
ERP
CSV
Excel
Manual
Partner feed
```

قد يحدث conflict.

لذلك:

```text
External sources
→ normalize
→ compare
→ detect conflict
→ resolve according to source precedence
→ manual review when ambiguous
→ canonical inventory
```

يجب تسجيل:

```text
value
source
timestamp
confidence
```

---

# 20. Pricing Domain

السعر ليس field واحد.

```text
Price
Payment Plan
Down Payment
Installments
Maintenance
Administrative Fees
Discounts
Promotions
Commission
Currency
Validity
```

## Price versioning

لا يعدّل التاريخ.

```text
Price v1
valid: Jan 1 → Jan 10

Price v2
valid: Jan 11 →
```

أي Offer أو Deal يحتفظ بنسخة السعر الذي استُخدم وقتها.

---

# 21. Listing Domain

### قاعدة أساسية:

**Property Asset ≠ Listing**

العقار هو الأصل.

Listing هي طريقة عرضه تجاريًا.

```text
PropertyAsset
 ↓
Listing
```

Listing تحتوي:

```text
title
description
asking_price
marketing_content
media
status
publication settings
```

يمكن لنفس Asset أن يكون له أكثر من Listing حسب القناة أو السوق.

---

# 22. Listing Distribution

```text
Listing
 ↓
Syndication Engine
 ├── Website
 ├── Social
 ├── Property Portals
 ├── Partner Network
 └── Internal marketplace
```

لكل channel:

```text
draft
pending
published
rejected
paused
expired
unpublished
```

---

# 23. Property Media

Media منفصلة عن property logic:

```text
images
videos
floor_plans
brochures
documents
virtual_tours
```

والـAI يستطيع:

```text
describe
classify
extract
tag
search
```

لكن لا يغيّر property facts بدون validation.

---

# 24. Search Architecture

البداية:

```text
PostgreSQL
+
Full Text Search
+
pgvector
```

ثلاثة أنماط:

### Structured

```text
price <= 8M
bedrooms = 3
area = New Cairo
```

### Semantic

```text
شقة هادية مناسبة لعيلة
```

### Hybrid

```text
hard filters
+
semantic retrieval
+
business ranking
```

لا تستخدم vector search للسعر أو availability أو permissions.

---

# 25. Property Matching Engine

pipeline:

```text
Customer Request
 ↓
Requirement Extraction
 ↓
Hard Constraint Filtering
 ↓
Candidate Generation
 ↓
Semantic Retrieval
 ↓
Business Ranking
 ↓
Behavioral Re-ranking
 ↓
Top Matches
```

### Ranking signals

```text
budget_fit
location_fit
bedroom_fit
property_type_fit
payment_fit
delivery_fit
view_fit
finishing_fit
behavioral_fit
freshness
availability_confidence
```

---

# 26. Sales Domain

Lead لا يساوي Opportunity.

```text
Lead
 ↓
Opportunity
```

Opportunity تمثل فرصة تجارية حقيقية مرتبطة غالبًا بـ:

```text
customer
property
unit/listing
sales owner
stage
estimated value
next action
```

---

# 27. Opportunity State Machine

```text
DISCOVERY
 ↓
QUALIFIED
 ↓
MATCHED
 ↓
VIEWING
 ↓
OFFER
 ↓
NEGOTIATION
 ↓
RESERVATION
 ↓
WON
```

Side state:

```text
LOST
```

---

# 28. Sales Routing

Lead assignment لا يكون Round Robin فقط.

يأخذ في الاعتبار:

```text
area expertise
project expertise
language
lead value
current workload
availability
branch
team
existing relationship
```

مع fallback.

---

# 29. SLA Engine

مثال:

```text
Lead Created
↓
SLA = 60s
```

ثم:

```text
30s → reminder
60s → escalate
120s → reassign
```

SLA engine deterministic.

---

# 30. Viewing Domain

```text
viewings
viewing_participants
viewing_outcomes
viewing_feedback
```

Viewing مرتبطة بـ:

```text
customer
lead
opportunity
property/unit
salesperson
time
location
```

---

# 31. Viewing State Machine

```text
REQUESTED
 ↓
CONFIRMED
 ↓
ATTENDED
 ↓
COMPLETED
```

Alternative states:

```text
RESCHEDULED
CANCELLED
NO_SHOW
```

Rescheduling يفضل أن يسجل كحدث/تغيير زمني بدل جعل history تضيع.

---

# 32. Smart Scheduling

Scheduling engine يراعي:

```text
customer availability
sales availability
property availability
working hours
buffers
travel time
existing appointments
```

الـAI يمكنه اقتراح وقت، لكن Calendar/Scheduling Service هو الذي يتحقق.

---

# 33. Offer Domain

```text
Offer
├── customer
├── opportunity
├── property/unit
├── price snapshot
├── payment plan snapshot
├── discount
├── terms
├── validity
├── approval
└── status
```

## State machine

```text
DRAFT
 ↓
PENDING_APPROVAL
 ↓
SENT
 ↓
COUNTERED
 ↓
ACCEPTED
```

Alternative:

```text
REJECTED
EXPIRED
WITHDRAWN
```

---

# 34. Negotiation

يتم الاحتفاظ بتاريخ:

```text
Offer v1
→ Counter v1
→ Offer v2
→ Counter v2
→ Accepted
```

الـAI يستطيع:

* تلخيص موقف العميل
* استخراج objections
* اقتراح صياغة
* اقتراح next step

لكن لا يستطيع تجاوز pricing/discount policy.

---

# 35. Reservation Domain

Reservation هي عملية تجارية منفصلة عن Offer.

```text
Offer accepted
 ↓
Reservation request
 ↓
validation
 ↓
concurrency-safe inventory operation
 ↓
reservation
 ↓
inventory state change
```

Reservation يجب أن تكون:

```text
idempotent
audited
transactional
time-bound when applicable
```

---

# 36. Contract & Document Domain

File storage ليست Document Management.

## Document entities

```text
documents
document_versions
document_approvals
document_signatures
document_links
```

أنواع:

```text
Offer
Reservation Form
Contract
Broker Agreement
Commission Agreement
Property Documents
Customer Documents
Identity Documents
```

كل Document له:

```text
version
status
source
effective_at
expires_at
owner
approval
```

---

# 37. Transaction & Finance Domain

ليس Accounting ERP كاملًا في V1.

لكن يجب دعم:

```text
payments
payment_schedules
invoices
fees
discounts
refunds
commissions
commission_rules
payouts
```

---

# 38. Commission Engine

Commission rules يجب أن تكون configurable.

قد توجد:

```text
Agency share
Sales Rep share
Broker share
Referral partner share
Developer commission
```

ويتم حسابها من Deal state والقواعد المعتمدة.

---

# 39. Deal Domain

Deal تمثل النتيجة التجارية.

```text
Deal
├── customer
├── property/unit
├── opportunity
├── contract
├── financials
├── commissions
├── participants
├── status
└── closed_at
```

## Deal states

```text
OPEN
 ↓
CONTRACTED
 ↓
WON
```

Alternative:

```text
LOST
CANCELLED
```

---

# 40. Marketing & Acquisition Domain

يجب تتبع رحلة acquisition بالكامل:

```text
Campaign
 ↓
Source
 ↓
Lead
 ↓
Qualified
 ↓
Viewing
 ↓
Reservation
 ↓
Deal
 ↓
Revenue
```

## Core entities

```text
campaigns
campaign_channels
lead_sources
utm_parameters
landing_pages
forms
audiences
attributions
marketing_assets
```

---

# 41. Attribution

لكل Lead:

```text
first_touch
last_touch
source
medium
campaign
content
landing_page
referrer
```

ثم ربطها بالـOpportunity/Deal.

الهدف:

```text
ad spend
→ leads
→ qualified leads
→ viewings
→ reservations
→ revenue
```

---

# 42. Marketing Content Engine

AI يساعد في:

```text
listing titles
property descriptions
WhatsApp copy
Instagram captions
ad copy
email copy
video scripts
campaign variants
```

لكن:

```text
Generate
→ Review
→ Approve
→ Publish
```

خصوصًا للمحتوى المدفوع أو الذي يمثل التزامًا تجاريًا.

---

# 43. Automation Platform

كل Domain يولد Events.

Automation engine يراقبها.

```text
Trigger
 ↓
Condition
 ↓
Action
 ↓
Wait
 ↓
Condition
 ↓
Action
```

---

# 44. Journey Engine

مثال:

```text
Lead Qualified
 ↓
Find Matches
 ↓
Send Properties
 ↓
Wait
 ↓
No Response?
 ↓
Follow-up
 ↓
Interested?
 ├── Yes → Viewing
 └── No  → Nurture
```

Journey يجب أن تكون durable.

الانتظار لا يعتمد على process memory.

كل state محفوظ في DB/Workflow runtime.

---

# 45. Workflow Technology

### Ordinary jobs

تستخدم:

```text
Redis
+
Worker pool
```

لـ:

* embeddings
* notifications
* document processing
* AI jobs
* sync
* reports

### Long-running workflows

استخدم durable workflow engine مثل:

```text
Temporal
```

لـ:

* multi-day nurturing
* reminders
* approval workflows
* reservation expiration
* long-running campaigns
* human-in-the-loop journeys

ولا تستخدم workflow engine لكل API request بسيط.

---

# 46. Event Architecture

كل حدث business مهم له Event name واضح.

## Lead

```text
lead.created
lead.updated
lead.qualified
lead.assigned
lead.score_changed
lead.dormant
lead.reactivated
lead.converted
lead.lost
```

## Conversation

```text
conversation.created
message.received
message.sent
message.delivered
message.read
message.failed
call.started
call.completed
```

## Property / Inventory

```text
property.created
property.updated
unit.available
unit.held
unit.released
unit.reserved
unit.contracted
unit.sold
inventory.conflict_detected
```

## Listings

```text
listing.created
listing.updated
listing.published
listing.paused
listing.expired
listing.unpublished
```

## Sales

```text
opportunity.created
opportunity.stage_changed
viewing.requested
viewing.confirmed
viewing.rescheduled
viewing.cancelled
viewing.no_show
viewing.completed
offer.created
offer.sent
offer.countered
offer.accepted
offer.rejected
reservation.created
reservation.expired
reservation.cancelled
```

## Finance

```text
contract.created
payment.scheduled
payment.created
payment.completed
payment.failed
payment.refunded
commission.created
commission.approved
commission.paid
deal.closed
```

---

# 47. Transactional Outbox

أي mutation مهمة:

```text
BEGIN

UPDATE business state

INSERT outbox_event

COMMIT
```

ثم worker:

```text
outbox
→ publish/process
→ retry
→ dead letter
```

هذا يمنع ضياع الأحداث بين transaction والـqueue.

---

# 48. Event Idempotency

كل event processing يستخدم:

```text
event_id
consumer_id
processed_at
```

وكل external mutation يستخدم:

```text
idempotency_key
```

حتى:

```text
retry ≠ duplicate business action
```

---

# 49. Webhook Architecture

```text
Receive
 ↓
Verify signature
 ↓
Persist raw payload
 ↓
Deduplicate
 ↓
Normalize
 ↓
Create internal event
 ↓
Process async
 ↓
Acknowledge
```

يجب ألا يتم تنفيذ processing ثقيل داخل webhook request نفسه.

---

# 50. Dead Letter Queue

event الفاشل:

```text
failed
 ↓
retry
 ↓
backoff
 ↓
retry
 ↓
retry
 ↓
DLQ
```

مع Admin interface لمعرفة:

```text
what failed
why
when
retry count
last error
```

---

# 51. AI Platform

الـAI ليس Agent واحد.

```text
AI PLATFORM
│
├── Agent Runtime
├── Context Engine
├── Model Gateway
├── Tool Gateway
├── Memory
├── Knowledge
├── Retrieval
├── Guardrails
├── Voice Runtime
└── Evaluation
```

---

# 52. Customer-Facing AI Agents

### Reception Agent

يفهم الرسائل ويوجه conversation.

### Qualification Agent

يجمع requirements.

### Property Matching Agent

يبحث ويرتب العقارات.

### Scheduling Agent

ينسق المعاينة.

### Follow-up Agent

يتابع.

### Reactivation Agent

يعيد تشغيل الـdormant leads.

### Voice Agent

يتعامل مع المكالمات.

---

# 53. Internal AI Agents

### Sales Copilot

* conversation summary
* suggested reply
* next best action
* objection handling
* customer context

### Manager Copilot

* team summary
* pipeline
* SLA violations
* leakage

### Inventory Assistant

* supply changes
* stale inventory
* conflicts
* high-demand units

### Marketing Copilot

* campaign content
* segmentation
* performance analysis

### Analytics Agent

* natural language queries
* business insights

---

# 54. Agent Runtime Contract

كل Agent:

```text
agent
├── name
├── purpose
├── system instructions
├── model profile
├── tools
├── knowledge scopes
├── memory policy
├── permissions
├── guardrails
├── escalation rules
├── max steps
├── timeout
├── version
└── evaluation suite
```

---

# 55. Agent Execution Rule

لا توجد open-ended autonomous loops بلا حدود.

لكل execution:

```text
max_steps
max_latency
max_cost
allowed_tools
allowed_actions
```

Agent يوقف نفسه عندما:

```text
task complete
need human
insufficient data
policy violation
tool failure
budget exceeded
```

---

# 56. Context Engine

لا ترسل entire history إلى كل model call.

السياق:

```text
Current Message
+
Recent Conversation
+
Structured Lead State
+
Relevant Customer Facts
+
Relevant Property Data
+
Relevant Knowledge
+
Available Tools
+
Policies
```

مع token budget واضح.

---

# 57. Memory Architecture

### Short-term

المحادثة الحالية.

### Structured memory

```text
budget
area
bedrooms
preferences
objections
timeline
```

### Long-term history

```text
calls
messages
viewings
offers
reservations
deals
```

ولا تستخدم LLM memory كبديل للـCRM.

---

# 58. Tool Gateway

Tools تقسم إلى:

### Read tools

```text
search_properties()
get_property()
get_unit()
get_current_price()
get_payment_plan()
check_availability()
get_customer_history()
find_viewing_slots()
```

### Write tools

```text
create_lead()
assign_lead()
create_task()
create_viewing()
send_message()
create_offer()
create_reservation()
```

كل tool له:

```text
input schema
authorization policy
tenant scope
audit
idempotency
timeout
```

---

# 59. AI Write Permissions

AI write actions classified إلى:

### Safe automatic

```text
create internal note
create follow-up task
update non-sensitive preferences
summarize conversation
```

### Controlled automatic

```text
send approved message
book standard viewing
create internal task
```

### Approval-required

```text
discount
special offer
contract
financial changes
high-value outbound
reservation under special conditions
```

---

# 60. Guardrails

الـAI ممنوع من:

```text
invent price
invent availability
invent unit
invent booking
invent customer history
invent discount
invent policy
bypass permissions
```

عند غياب المعلومة:

```text
retrieve
verify
or ask
```

ولا يتم التخمين في transaction-critical data.

---

# 61. Model Gateway

لا تربط النظام مباشرة بProvider واحد.

```text
AI Gateway
├── Fast model profile
├── Standard model profile
├── Reasoning model profile
├── Embedding model
├── STT
└── TTS
```

المهمة تختار model profile.

مثلاً:

```text
classification → fast
normal customer conversation → fast/standard
complex analysis → reasoning
voice → low-latency model chain
embedding → embedding provider
```

---

# 62. Prompt / Agent Versioning

لا يوجد:

```text
current_prompt
```

فقط.

بل:

```text
agent_version
prompt_version
tool_version
knowledge_version
ranking_version
```

كل execution يسجل النسخ التي استخدمها.

---

# 63. AI Evaluation

قبل نشر Agent version جديدة:

```text
intent accuracy
tool selection accuracy
tool argument accuracy
retrieval quality
response quality
hallucination rate
human escalation
latency
cost
business outcome
```

استخدم regression dataset حقيقي مجهول الهوية/مصمم للاختبار.

---

# 64. Voice Architecture

```text
Telephony
 ↓
Realtime audio
 ↓
STT
 ↓
Conversation runtime
 ↓
Context Engine
 ↓
LLM
 ↓
Tool Gateway
 ↓
TTS
 ↓
Telephony
```

Voice Agent يستخدم نفس:

```text
Customer
Conversation
Property
Availability
Scheduling
Sales
```

ولا يكون CRM منفصلًا.

---

# 65. Conversation Intelligence

كل conversation يمكن استخراج منها:

```text
intent
requirements
budget
objections
timeline
preferred area
property interests
next step
commitments
```

وتكتب في structured state.

لكن source provenance مهم:

```text
explicitly stated
inferred
system-derived
```

---

# 66. Next Best Action Engine

لكل Lead/Opportunity:

```text
next_action
next_action_at
reason
confidence
```

Possible actions:

```text
CALL
SEND_PROPERTIES
SEND_ALTERNATIVE
FOLLOW_UP
BOOK_VIEWING
REACTIVATE
ESCALATE
WAIT
```

ويتم حسابها بناءً على business state + AI recommendations.

---

# 67. Reactivation Engine

الـdormant leads تعتبر asset.

```text
Dormant Leads
 ↓
segment
 ↓
new matching inventory
 ↓
personalized outreach
 ↓
response
 ↓
qualification
 ↓
viewing
```

لا ترسل spam.

يحترم consent وfrequency rules.

---

# 68. Customer 360

الصفحة الرئيسية للعميل:

```text
Customer
├── Profile
├── Identity
├── Preferences
├── Lead
├── Intent
├── Score
├── Conversations
├── Calls
├── Properties Viewed
├── Saved Properties
├── Viewings
├── Offers
├── Reservations
├── Contracts
├── Payments
├── Tasks
├── Documents
└── Timeline
```

---

# 69. Communication Timeline

كل شيء زمني:

```text
Lead created
AI qualification
Property viewed
Call
WhatsApp
Viewing booked
Viewing attended
Offer
Counter offer
Reservation
Payment
```

ولا يوجد تعديل تاريخي صامت.

---

# 70. Analytics Architecture

### Operational Analytics

```text
active leads
unassigned leads
upcoming viewings
SLA breaches
inventory alerts
```

### Sales Analytics

```text
lead conversion
viewing rate
show-up rate
offer rate
reservation rate
deal rate
```

### Marketing Analytics

```text
cost per lead
cost per qualified lead
cost per viewing
revenue by campaign
```

### Inventory Analytics

```text
inventory velocity
stale inventory
demand per property
demand per area
```

### Revenue Analytics

```text
pipeline
weighted pipeline
revenue
commissions
deal velocity
```

---

# 71. Revenue Intelligence

الفunnel الرئيسي:

```text
Campaign
→ Lead
→ Qualified
→ Viewing
→ Offer
→ Reservation
→ Deal
→ Revenue
```

ويجب القدرة على drill-down:

```text
Company
→ Branch
→ Team
→ Sales Rep
→ Campaign
→ Project
→ Unit
```

---

# 72. AI Insights

AI لا يختلق insight.

Pipeline:

```text
Events
→ Metrics
→ Statistical patterns
→ AI explanation
→ Insight
```

مثلاً:

```text
Qualified → Viewing conversion declined
```

ثم AI يشرح فقط ما تدعمه البيانات المتاحة.

---

# 73. Natural Language Analytics

المدير يكتب:

```text
هات كل العملاء اللي ميزانيتهم فوق 6 مليون
وعايزين 3 غرف
ومحدش كلمهم من أسبوع
```

المسار:

```text
Natural Language
→ Intent parser
→ Structured query plan
→ Authorization
→ Analytics/Data service
→ Result
→ Natural-language explanation
```

الـLLM لا يولد SQL ويشغله على DB مباشرة.

---

# 74. Market Intelligence — Extension

يضاف فوق internal intelligence:

```text
External Market Data
+
Comparable Properties
+
Area Trends
+
External Listings
+
Supply/Demand Signals
```

ويظل منفصلًا عن transaction-critical inventory.

---

# 75. Post-Sale — Extension

لـbrokerage في البداية:

```text
Customer support
Payment reminders
Referral
Resale opportunity
```

أما developer-style:

```text
Handover
Maintenance
Complaints
Warranty
```

فيعتبر Module توسع وليس Core V1.

---

# 76. Broker Network — Extension

يمكن لاحقًا:

```text
Agency A
↔
Agency B
```

مع:

```text
shared listings
referrals
commission splits
permissions
partner agreements
deal visibility
```

---

# 77. Integration Hub

Integration Hub جزء أساسي من الـPlatform.

```text
INTEGRATION HUB
├── Messaging
├── Telephony
├── Calendar
├── CRM
├── ERP
├── Developer APIs
├── Property Portals
├── Ads
├── Payments
├── Email
├── SMS
└── Partner APIs
```

كل connector له:

```text
credentials
authentication
webhooks
sync
mapping
retry
rate limits
health
logs
```

---

# 78. Integration Sync Model

لكل external entity:

```text
internal_id
external_id
provider
last_synced_at
sync_status
source_version
```

ولا تحذف internal state تلقائيًا لمجرد أن provider اختفى إلا حسب reconciliation rules.

---

# 79. API Architecture

REST:

```text
/api/v1/...
```

مع:

```text
cursor pagination
filtering
sorting
search
idempotency
validation
authorization
versioning
```

Core endpoints:

```text
/v1/leads
/v1/people
/v1/conversations
/v1/properties
/v1/projects
/v1/units
/v1/inventory
/v1/listings
/v1/viewings
/v1/opportunities
/v1/offers
/v1/reservations
/v1/contracts
/v1/deals
/v1/payments
/v1/commissions
/v1/campaigns
/v1/agents
/v1/workflows
/v1/journeys
/v1/analytics
/v1/integrations
```

---

# 80. API Rules

كل mutation يجب أن يمر:

```text
Authentication
→ Tenant Resolution
→ Authorization
→ Validation
→ Domain Rules
→ Transaction
→ Audit
→ Event
```

---

# 81. Database Architecture

PostgreSQL هو المصدر الأساسي.

Recommended stack:

```text
PostgreSQL
SQLAlchemy 2.x
asyncpg
Alembic
Pydantic
FastAPI
```

مع:

```text
pgvector
PostgreSQL Full Text Search
```

عندما يحتاج النظام search متخصصًا جدًا، يمكن إضافة OpenSearch لاحقًا بدون تغيير business source of truth.

---

# 82. Row-Level Security

لو استخدمنا Supabase/Postgres managed infrastructure:

```text
RLS
+
Application authorization
+
Tenant checks
```

وليس RLS وحده.

كل table business-critical يجب أن تكون مرتبطة بالـtenant بطريقة يمكن إثباتها في access path.

---

# 83. Caching

Redis يستخدم لـ:

```text
rate limits
short-lived cache
hot queries
temporary coordination
job queues
locks where appropriate
```

Redis ليس source of truth للعقارات أو reservations.

---

# 84. Object Storage

الملفات الكبيرة:

```text
images
videos
audio
PDFs
documents
brochures
floor plans
```

تذهب إلى:

```text
S3-compatible object storage
```

Database تحتفظ بالـmetadata فقط.

---

# 85. Document Processing Pipeline

```text
Upload
 ↓
Security scan
 ↓
Metadata extraction
 ↓
OCR / Vision where needed
 ↓
Normalization
 ↓
Knowledge indexing
 ↓
Vectorization where useful
```

لا تعتبر document extracted facts حقيقية حتى تمر validation/approval عندما تكون transaction-sensitive.

---

# 86. Repository Structure

```text
backend/
├── app/
│   ├── core/
│   │   ├── config/
│   │   ├── security/
│   │   ├── tenancy/
│   │   ├── permissions/
│   │   ├── errors/
│   │   └── idempotency/
│   │
│   ├── identity/
│   ├── organizations/
│   ├── contacts/
│   ├── leads/
│   ├── conversations/
│   ├── channels/
│   │
│   ├── properties/
│   ├── projects/
│   ├── inventory/
│   ├── listings/
│   ├── pricing/
│   ├── mandates/
│   │
│   ├── opportunities/
│   ├── matching/
│   ├── sales/
│   ├── viewings/
│   ├── offers/
│   ├── reservations/
│   ├── contracts/
│   ├── deals/
│   ├── finance/
│   │
│   ├── marketing/
│   ├── attribution/
│   │
│   ├── automation/
│   ├── journeys/
│   ├── tasks/
│   ├── notifications/
│   │
│   ├── ai/
│   │   ├── runtime/
│   │   ├── agents/
│   │   ├── tools/
│   │   ├── gateway/
│   │   ├── memory/
│   │   ├── knowledge/
│   │   ├── retrieval/
│   │   ├── guardrails/
│   │   ├── voice/
│   │   └── evaluation/
│   │
│   ├── analytics/
│   ├── revenue_intelligence/
│   ├── market_intelligence/
│   ├── integrations/
│   ├── billing/
│   ├── audit/
│   └── observability/
│
├── workers/
│   ├── ai/
│   ├── notifications/
│   ├── sync/
│   ├── documents/
│   ├── analytics/
│   └── webhooks/
│
├── migrations/
├── tests/
├── scripts/
└── docs/
```

---

# 87. Domain Module Internal Structure

كل Domain لا يكون folder مليان routes فقط.

الشكل:

```text
domain/
├── api/
├── schemas/
├── domain/
│   ├── entities/
│   ├── value_objects/
│   ├── state_machine/
│   └── policies/
├── application/
│   ├── commands/
│   ├── queries/
│   └── services/
├── infrastructure/
│   ├── repositories/
│   └── adapters/
└── events/
```

الهدف:

```text
API
≠
Business logic
≠
DB
```

---

# 88. Domain Service Rules

Example:

```text
ReservationService
```

مسؤوليته:

```text
validate reservation
check inventory
apply pricing snapshot
enforce permissions
create reservation
update inventory
emit event
```

ولا يجب أن يعرف:

```text
WhatsApp
LLM provider
frontend
```

---

# 89. Frontend Architecture

```text
Next.js / React
```

تقسيم الواجهة حسب business surface:

```text
Overview

Inbox
Leads
Customers
Sales Pipeline
Viewings
Deals

Properties
Projects
Units
Inventory
Listings

Marketing
Campaigns
Attribution

AI Agents
Knowledge
Automations
Journeys

Analytics
Revenue Intelligence
Market Intelligence

Tasks
Team

Integrations
Billing
Security
Audit
Settings
```

---

# 90. Frontend State Rules

Frontend لا يكون owner للـbusiness state.

```text
UI state
→ frontend

Business state
→ backend
```

استخدم server-side pagination/filtering للبيانات الكبيرة.

---

# 91. URL as Screen State

الشاشات يجب أن تكون deep-linkable.

مثال:

```text
/leads
/leads/123
/leads/123?tab=activity
/leads?status=qualified&owner=456&sort=-created_at
/properties/123?tab=inventory
/opportunities/582?tab=offers
```

الفلاتر والـsorting والـpagination المهمة تكون قابلة للمشاركة والاسترجاع من URL.

---

# 92. UX Safety

للحركات القابلة للعكس:

```text
Action
→ immediate confirmation
→ Undo toast
```

الحركات الثقيلة:

```text
bulk action
→ create job
→ progress
→ result
```

لا تعلق الواجهة أثناء عمليات bulk طويلة.

---

# 93. Permissions

الأدوار الأساسية:

```text
Owner
Admin
Sales Manager
Sales
Marketing
Finance
Operations
Viewer
External Partner
```

لكن authorization يجب أن يدعم:

```text
Role
+
Branch
+
Team
+
Resource ownership
+
Explicit permission
```

---

# 94. Audit System

يتم تسجيل:

```text
who
what
when
entity
before
after
source
request_id
```

خصوصًا:

```text
price changes
inventory
assignments
permissions
reservations
offers
contracts
payments
AI actions
```

---

# 95. AI Action Ledger

كل Agent execution مهم يسجل:

```text
execution_id
tenant_id
agent_id
agent_version
conversation_id
model
prompt_version
tools_used
tool_arguments
tool_results
authorization
latency
tokens
cost
outcome
human_handoff
```

هذا أساس debugging والـAI governance.

---

# 96. Observability

كل request يجب أن يكون له:

```text
request_id
trace_id
```

نحتاج:

```text
logs
metrics
traces
errors
latency
queue depth
provider health
AI costs
tool failures
```

والـcritical flow يجب أن يكون traceable end-to-end:

```text
Webhook
→ Lead
→ AI
→ Search
→ Tool
→ DB
→ Event
→ Message
```

---

# 97. Reliability

كل provider integration:

```text
timeout
retry
exponential backoff
circuit breaker
dead letter
health state
```

ولا يوجد external call بلا timeout.

---

# 98. Rate Limiting

مستويان:

### Platform

```text
per IP
per API key
per user
```

### Tenant

```text
messages
AI concurrency
voice concurrency
webhooks
automation executions
API rate
```

Tenant واحد لا يستطيع ابتلاع موارد كل العملاء.

---

# 99. Billing & Usage

كل Tenant له:

```text
subscription
plan
seats
messages
voice_minutes
AI_requests
storage
automation_runs
```

ويحسب النظام:

```text
Revenue
-
AI provider cost
-
Voice cost
-
Messaging cost
-
Infrastructure cost
=
Gross margin
```

---

# 100. Feature Flags

Features الكبيرة خلف flags:

```text
voice_agent
smart_matching_v2
ai_reactivation
market_intelligence
broker_network
advanced_deals
```

يمكن تشغيلها:

```text
per tenant
per organization
per environment
```

---

# 101. Testing Strategy

## Unit

Domain rules.

## Integration

Database + integrations.

## Concurrency

خصوصًا:

```text
reservation
inventory
payments
```

## Workflow

Journeys وSLA.

## AI

```text
intent
tool choice
tool arguments
retrieval
response
guardrails
```

## Regression

كل Prompt/Model/Agent version جديد يجب أن يمر على evaluation suite.

## Failure scenarios

اختبر:

```text
provider timeout
duplicate webhook
duplicate reservation request
DB retry
worker crash
message delivery failure
stale inventory
concurrent booking
```

---

# 102. Critical Invariants

هذه يجب أن تكون اختبارات دائمة:

### Inventory

```text
One unit
→ cannot have two active reservations
```

### Tenant

```text
Tenant A
→ cannot read/write Tenant B
```

### AI

```text
Agent
→ cannot perform unauthorized action
```

### Price

```text
Offer
→ uses immutable price snapshot
```

### Event

```text
DB commit
→ event cannot silently disappear
```

### Messaging

```text
Retry
→ cannot create duplicate business message/action unintentionally
```

---

# 103. Security Boundary

```text
Authentication
 ↓
Tenant Resolution
 ↓
Authorization
 ↓
Resource Authorization
 ↓
Domain Validation
 ↓
DB Transaction
```

كل layer defense-in-depth.

---

# 104. Data Freshness

خصوصًا للعقارات:

كل critical fact:

```text
availability
price
payment plan
listing publication
```

له:

```text
source
last_updated_at
effective_at
freshness state
```

إذا data أصبحت stale:

```text
STALE
```

ولا يتعامل معها الـAI كحقيقة حالية بدون verification.

---

# 105. Property Search Index

أوصي بإنشاء read/search representation مخصصة:

```text
property_search_document
```

تحتوي على البيانات اللازمة للبحث والترتيب.

لكن:

**Search Index ≠ source of truth**

عند الحاجة للحجز/السعر/availability يرجع النظام إلى canonical domain.

---

# 106. Analytics Read Model

لا تجعل Dashboard الثقيلة تضرب transactional tables مباشرة دائمًا.

ابدأ بـ:

```text
Postgres
→ indexed read models / materialized views
```

وعندما يكبر النظام:

```text
events
→ warehouse
→ analytics models
```

---

# 107. Scaling Strategy

## Stage 1

```text
FastAPI
Postgres
Redis
Workers
Storage
```

## Stage 2

```text
API × N
Workers × N
AI workers × N
```

## Stage 3

إذا ظهر bottleneck حقيقي:

```text
AI workload isolated
Voice isolated
Search isolated
Analytics isolated
```

لا يتم تفكيك النظام لمجرد أن Microservices تبدو "أكثر احترافية".

---

# 108. Final Infrastructure

الـreference architecture:

```text
Frontend
→ Next.js / React

Backend
→ FastAPI

Database
→ PostgreSQL

ORM
→ SQLAlchemy 2.x

Migrations
→ Alembic

Validation
→ Pydantic

Async/Jobs
→ Redis + Workers

Durable workflows
→ Temporal where needed

Search
→ PostgreSQL FTS + pgvector

Storage
→ S3-compatible Object Storage

Auth
→ Managed identity/auth layer, e.g. Supabase Auth

Observability
→ OpenTelemetry-compatible stack

AI
→ Provider-agnostic Model Gateway

Voice
→ Telephony + STT + LLM + TTS
```

إذا استُخدم Supabase:

```text
Supabase
→ PostgreSQL
→ Auth
→ Storage / managed platform capabilities

FastAPI
→ ALL business/domain logic
→ AI tools
→ transactions
→ authorization
→ events
```

---

# 109. What Must NOT Be Done

## ممنوع

LLM → SQL مباشرة.

LLM → inventory update مباشرة.

LLM → arbitrary API calls.

Frontend → business-rule decisions.

Redis → source of truth.

Vector DB → source of truth.

Provider-specific code داخل domain.

Microservice لكل table.

Workflow engine لكل request.

Huge prompt يحتوي كل بيانات العميل دائمًا.

AI memory بدل structured CRM.

Automatic discount without policy.

Reservation without transaction/concurrency control.

Deleting historical price instead of versioning.

Silent mutation بدون audit.

---

# 110. Real Estate Domain Map

الصورة النهائية للـbusiness:

```text
                  REAL ESTATE REVENUE OS
                           │
       ┌───────────────────┼───────────────────┐
       ↓                   ↓                   ↓
 CUSTOMER              PROPERTY             ACQUISITION
 & ENGAGEMENT          & SUPPLY             & DISTRIBUTION
       │                   │                   │
 People                Developers           Campaigns
 Identities            Projects             Sources
 Leads                 Buildings            Attribution
 Conversations         Assets               Landing Pages
 Calls                 Units                Forms
 Inbox                 Inventory            Syndication
 Activities            Listings
 Tasks                 Pricing
 Owners                Mandates
 Partners              Supply Sources
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ↓
                      SALES ENGINE
                           │
                 Matching / Routing
                 Opportunities
                 Viewings
                 Offers
                 Negotiation
                 Reservations
                           ↓
                    TRANSACTION ENGINE
                           │
               Contracts / Payments
               Fees / Commissions
               Payouts
                           ↓
                         DEAL
                           │
                           ↓
                  REVENUE INTELLIGENCE
```

---

# 111. Intelligence Overlay

فوق كل الـDomains:

```text
                   AI / INTELLIGENCE
                          │
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
   CUSTOMER AI       PROPERTY AI        SALES AI
        │                 │                 │
 Qualification       Matching           Copilot
 Reception            Ranking            Next Action
 Voice                Search             Objections
 Follow-up            Recommendations    Routing
 Reactivation         Inventory          Analysis
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ↓
                   MANAGEMENT AI
                          │
                 Insights / Analytics
                 Revenue Intelligence
                 Forecasting
                 Market Intelligence
```

---

# 112. Automation Overlay

```text
EVENT
 ↓
RULE
 ↓
CONDITION
 ↓
ACTION
 ↓
WAIT
 ↓
CONDITION
 ↓
ACTION
```

Examples:

```text
lead.created
→ qualify

lead.qualified
→ matching

lead.hot
→ assign senior sales

viewing.confirmed
→ reminders

viewing.no_show
→ recovery journey

viewing.completed
→ feedback + next action

offer.accepted
→ reservation workflow

reservation.expiring
→ alerts

payment.failed
→ follow-up

deal.closed
→ referral journey
```

---

# 113. Golden End-to-End Flow

السيناريو الذي يجب أن يمر من خلال معظم المعمارية:

```text
Customer sends WhatsApp message
        ↓
Webhook verification
        ↓
Identity Resolution
        ↓
Conversation updated
        ↓
AI Intent Extraction
        ↓
Lead created/updated
        ↓
Requirements structured
        ↓
Property Matching
        ↓
Availability verification
        ↓
Properties recommended
        ↓
Customer selects property
        ↓
Scheduling Engine
        ↓
Viewing created
        ↓
Reminder Journey
        ↓
Viewing completed
        ↓
Feedback extraction
        ↓
Opportunity update
        ↓
Sales follow-up
        ↓
Offer
        ↓
Negotiation
        ↓
Reservation
        ↓
Inventory state change
        ↓
Contract
        ↓
Payment schedule
        ↓
Commission
        ↓
Deal closed
        ↓
Revenue analytics
        ↓
Referral / re-engagement
```

---

# 114. Implementation Order

الفريق لا يبني كل modules بالتوازي بشكل عشوائي.

## Phase 1 — Platform Core

```text
Tenant
Organization
Users
RBAC
Identity
Audit
Core API
Database
Events
Outbox
Idempotency
```

## Phase 2 — Engagement

```text
Contacts
Leads
Conversations
Messages
Channel abstraction
Unified Inbox
Activities
Tasks
```

## Phase 3 — Property & Supply

```text
Developers
Projects
Buildings
Assets
Units
Owners
Supply Sources
Mandates
Inventory
Pricing
Listings
```

## Phase 4 — Search & Matching

```text
Property Search
Full-text
pgvector
Matching
Ranking
Requirements
```

## Phase 5 — Sales

```text
Opportunities
Routing
Viewings
Scheduling
Offers
Negotiation
Reservations
```

## Phase 6 — Transactions

```text
Contracts
Payments
Commissions
Deals
```

## Phase 7 — AI

```text
Agent Runtime
Context
Tools
Knowledge
Guardrails
Sales Copilot
Customer AI
Voice
```

## Phase 8 — Automation

```text
Events
Rules
Journeys
Follow-up
SLA
Reactivation
Notifications
```

## Phase 9 — Intelligence

```text
Analytics
Revenue Intelligence
AI Insights
Marketing Attribution
Forecasting
```

## Phase 10 — Expansion

```text
Market Intelligence
Broker Network
Advanced Finance
Post-sale
Valuation
```

---

# 115. First Production Vertical

أول release تجاري لا يحتاج كل extension.

يجب أن يكون قويًا جدًا في هذا المسار:

```text
Lead
→ Qualification
→ Property Matching
→ Viewing
→ Follow-up
→ Offer
→ Reservation
```

مع:

```text
WhatsApp
Unified Inbox
CRM
Listings
Inventory
AI
Analytics
```

ثم يتم فتح Finance/Contracts/Advanced Brokerage تدريجيًا.

---

# 116. Definition of Done for V1

لا تعتبر النسخة production-ready إلا إذا:

```text
Multi-tenant isolation works
RBAC works
Inbound channels are normalized
Duplicate webhooks are safe
Lead creation is idempotent
Conversation history is consistent
Property inventory is transactional
Double reservation is impossible
Pricing is versioned
Listings are separate from assets
Matching uses hard + semantic filters
Viewing scheduling prevents conflict
Offers are versioned
AI cannot bypass domain rules
AI actions are auditable
Outbound respects consent
Events use transactional outbox
Retries are safe
Failed jobs go to DLQ
Critical flows are observable
AI runs are evaluable
```

---

# 117. Final Architecture Decision

هذه هي الـfinal architecture المعتمدة:

```text
ARCHITECTURAL STYLE
→ Modular Monolith
→ Event-driven internals
→ Domain-oriented boundaries

SYSTEM OF RECORD
→ PostgreSQL

MANAGED PLATFORM
→ Supabase can provide Postgres/Auth/Storage infrastructure

API / DOMAIN LAYER
→ FastAPI

ORM
→ SQLAlchemy

SEARCH
→ PostgreSQL FTS + pgvector

CACHE / JOBS
→ Redis + Workers

LONG-RUNNING WORKFLOWS
→ Temporal where justified

STORAGE
→ S3-compatible Object Storage

AI
→ Provider-agnostic Model Gateway
→ Tool-driven Agents
→ Structured Context
→ Guardrails
→ Evaluation

COMMUNICATION
→ Provider adapters behind Channel Gateway

BUSINESS SAFETY
→ State Machines
→ Transactions
→ Idempotency
→ Authorization
→ Audit

EVENTS
→ Domain Events
→ Transactional Outbox
→ Retry / DLQ

SCALING
→ Horizontal API/worker scaling
→ Split services only when justified by real bottlenecks
```

# 118. The Core Rule for the Engineering Team

أهم سطر في الوثيقة كلها:

```text
THE AI IS NOT THE SYSTEM.

THE DOMAIN SYSTEM IS THE SYSTEM.

AI IS THE INTELLIGENCE LAYER THAT USES THE SYSTEM SAFELY.
```

والتنفيذ يجب أن يحافظ دائمًا على:

```text
LLM
↓
understand / decide what to request
↓
Tool Gateway
↓
Authorization
↓
Domain Service
↓
Database Transaction
↓
Domain Event
↓
Automation / Analytics / Notification
```

بهذا الشكل يمكن تغيير:

* LLM
* Voice provider
* WhatsApp provider
* Search engine
* AI prompts
* Agent implementations

من غير إعادة بناء الـbusiness core.

# 119. Final Product Boundary

الـCore المنتج في النهاية هو:

```text
CUSTOMER & ENGAGEMENT
+
PROPERTY & SUPPLY
+
LISTINGS
+
ACQUISITION
+
SALES
+
TRANSACTIONS
+
AI
+
AUTOMATION
+
INTELLIGENCE
```

والـExtensions:

```text
MARKET INTELLIGENCE
POST-SALE
BROKER NETWORK
ADVANCED FINANCE
VALUATION
```

لا تتم إضافة Domain جديد إلا إذا ظهر business requirement حقيقي يثبت أن capability الموجودة لا تكفي.

# 120. Final Engineering Philosophy

ابنوا النظام على أساس أن:

```text
Transactions must be deterministic.
AI must be replaceable.
Providers must be replaceable.
Data must be auditable.
Events must be reliable.
Workflows must survive restarts.
Tenants must be isolated.
Search must not become source of truth.
Frontend must not own business rules.
```

والنتيجة المستهدفة ليست:

```text
CRM + Chatbot
```

بل:

```text
A transaction-safe,
multi-tenant,
AI-native,
event-driven,
real-estate revenue operating system.
```

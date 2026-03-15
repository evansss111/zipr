# ZIPR — Zero-overhead Interagent Protocol

A token-efficient communication language designed for AI agents to exchange
messages, delegate tasks, query state, and coordinate — without any human
readability requirements.

---

## Message Format

```
<SRC>-><DST>|<TYPE>:<BODY>{;<KEY>=<VAL>}*
```

- `SRC` / `DST` — agent IDs (short alphanumeric, e.g. `a1`, `planner`, `*` for broadcast)
- `TYPE` — 1-2 char message type (see below)
- `BODY` — comma-separated payload
- `;<KEY>=<VAL>` — optional context tags appended after semicolons

---

## Message Types

| Code | Full name    | Direction | Meaning                          |
|------|--------------|-----------|----------------------------------|
| `q`  | query        | any       | Ask for information              |
| `r`  | response     | any       | Reply to a query                 |
| `t`  | task         | any       | Assign a task                    |
| `a`  | ack          | any       | Acknowledge receipt/completion   |
| `e`  | error        | any       | Signal failure                   |
| `s`  | state        | any       | Share current state snapshot     |
| `b`  | broadcast    | one->*     | Send to all listeners            |
| `c`  | capability   | any       | Advertise or query capabilities  |
| `p`  | ping         | any       | Liveness check                   |
| `x`  | terminate    | any       | Cancel task or end session       |

---

## Body Conventions

- Key-value pairs: `key=val`
- Lists: `key=[a,b,c]`
- Nested: `key={a=1,b=2}`
- String values with spaces: `key="some phrase"`
- Boolean: `key=T` / `key=F`
- Null / unknown: `key=~`
- Reference to another message: `ref=#<MSG_ID>`

---

## Context Tags

Appended after the body, separated by `;`:

| Tag     | Meaning                        |
|---------|-------------------------------|
| `id=`   | Unique message ID              |
| `re=`   | Reply-to message ID            |
| `ts=`   | Timestamp (unix epoch)         |
| `ttl=`  | Time-to-live in seconds        |
| `pri=`  | Priority: 0 (low) – 9 (high)  |
| `ctx=`  | Task/conversation context name |
| `conf=` | Confidence score 0.0–1.0       |

---

## Examples

### Ping / Pong
```
a1->a2|p:~
a2->a1|p:ok
```

### Query and response
```
scout->base|q:loc=enemy;ctx=mission1;id=m001
base->scout|r:loc={x=42,y=17},status=confirmed;re=m001;conf=0.91
```

### Task assignment
```
planner->worker|t:action=search,target="config files",path=/etc;id=t01;pri=7
worker->planner|a:status=started,eta=12s;re=t01
worker->planner|r:found=[/etc/app.conf,/etc/db.conf],count=2;re=t01
```

### Capability advertisement (broadcast)
```
agent7->*|c:caps=[search,summarize,translate],lang=[en,fr,de];id=c001
```

### Error
```
worker->planner|e:code=404,msg="target not found",target=/etc/secret;re=t01
```

### State snapshot
```
monitor->log|s:cpu=0.72,mem=0.41,tasks=[t01,t02],queue=3;ts=1741996800
```

### Multi-hop chain
```
ui->planner|t:goal="find all TODOs in repo";id=g01
planner->scout|t:action=grep,pattern=TODO,scope=/src;id=t02;ctx=g01
scout->planner|r:matches=47,files=["main.py","utils.py"];re=t02;ctx=g01
planner->ui|r:summary="47 TODOs in 2 files";re=g01
```

---

## Design Principles

1. **Token-first** — every character earns its place
2. **Flat by default** — avoid nesting unless necessary
3. **Typed messages** — the `TYPE` code carries semantic meaning
4. **Context threads** — `ctx=` and `re=` let agents chain conversations
5. **No schema enforcement** — agents negotiate meaning dynamically

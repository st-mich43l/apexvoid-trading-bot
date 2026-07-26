namespace ApexVoid.CTraderFeed;

// Fenced candidate-execution Lua. Every mutation is token-owned: the caller
// must prove candidate identity, stream-event identity and the exact current
// lease token before Redis rewrites the record. Records are decoded with
// cjson (never regex) and timestamps come from Redis `TIME` so lease expiry
// never depends on executor host clocks.
public static class CandidateExecutionScripts
{
  // Acquire result codes shared with CandidateClaimDisposition.
  public const int AcquireClaimed = 1;
  public const int AcquireActiveElsewhere = 2;
  public const int AcquireTerminal = 3;
  public const int AcquireRecoveryRequired = 4;
  public const int AcquireConflict = 5;

  private const string Prelude = """
    local NIL = cjson.null

    local TERMINAL = {
      ordered = true,
      completed = true,
      rejected = true,
      dry_run = true,
    }
    local RETRYABLE = {
      published = true,
      retryable_error = true,
    }
    local ACTIVE = {
      processing = true,
      broker_submitting = true,
    }
    local RECOVERY = {
      broker_outcome_unknown = true,
    }
    -- Lease-owned transitions that never finalise the candidate. Terminal
    -- outcomes go through the complete script instead.
    local ALLOWED_TRANSITIONS = {
      processing = {
        broker_submitting = true,
        broker_outcome_unknown = true,
        retryable_error = true,
      },
      broker_submitting = {
        broker_outcome_unknown = true,
      },
      broker_outcome_unknown = {
        retryable_error = true,
      },
    }
    -- Terminal outcomes the complete script may write, keyed by current state.
    local ALLOWED_COMPLETIONS = {
      processing = { ordered = true, rejected = true, completed = true },
      broker_submitting = { ordered = true, rejected = true, completed = true },
      broker_outcome_unknown = { ordered = true, rejected = true, completed = true },
    }

    local function now_seconds()
      local parts = redis.call('TIME')
      return tonumber(parts[1])
    end

    local function nz(value)
      if value == nil or value == NIL then
        return nil
      end
      if value == '' then
        return nil
      end
      return value
    end

    local function legacy_record(state, outcome)
      return {
        candidate_id = nil,
        stream_event_id = nil,
        state = state,
        lease_token = nil,
        lease_expires_at = nil,
        attempt = 0,
        last_error = nil,
        outcome = outcome,
        updated_at = 0,
        version = 1,
        legacy = true,
      }
    end

    -- Returns nil when the key is absent, or a normalised record table. An
    -- unparseable or unknown payload yields state `__invalid__` so callers
    -- surface an integrity conflict instead of guessing retryability.
    local function decode_record(raw)
      if raw == false or raw == nil then
        return nil
      end
      if string.sub(raw, 1, 1) == '{' then
        local ok, decoded = pcall(cjson.decode, raw)
        if not ok or type(decoded) ~= 'table' then
          return legacy_record('__invalid__', nil)
        end
        local state = nz(decoded.state)
        if state == nil then
          return legacy_record('__invalid__', nil)
        end
        return {
          candidate_id = nz(decoded.candidate_id),
          stream_event_id = nz(decoded.stream_event_id),
          state = state,
          lease_token = nz(decoded.lease_token),
          lease_expires_at = tonumber(nz(decoded.lease_expires_at)),
          attempt = tonumber(nz(decoded.attempt)) or 0,
          last_error = nz(decoded.last_error),
          outcome = nz(decoded.outcome),
          updated_at = tonumber(nz(decoded.updated_at)) or 0,
          version = tonumber(nz(decoded.version)) or 1,
          legacy = false,
        }
      end
      if raw == 'published' then
        return legacy_record('published', nil)
      end
      if raw == 'processing' then
        return legacy_record('processing', nil)
      end
      if raw == 'dry_run' then
        return legacy_record('dry_run', raw)
      end
      if string.sub(raw, 1, 8) == 'ordered:' then
        return legacy_record('ordered', raw)
      end
      if string.sub(raw, 1, 9) == 'rejected:' then
        return legacy_record('rejected', raw)
      end
      if string.sub(raw, 1, 13) == 'flip_pending:' then
        return legacy_record('completed', raw)
      end
      return legacy_record('__invalid__', nil)
    end

    local function encode_record(record)
      return cjson.encode({
        candidate_id = record.candidate_id or '',
        stream_event_id = record.stream_event_id or NIL,
        state = record.state,
        lease_token = record.lease_token or NIL,
        lease_expires_at = record.lease_expires_at or NIL,
        attempt = record.attempt or 0,
        last_error = record.last_error or NIL,
        outcome = record.outcome or NIL,
        updated_at = record.updated_at or 0,
        version = record.version or 1,
      })
    end

    -- Legacy records carry no identity, so a missing field cannot fail the
    -- comparison during a rolling deploy. A present mismatch always fails.
    local function identity_matches(record, candidate_id, stream_event_id)
      if record.candidate_id ~= nil and record.candidate_id ~= candidate_id then
        return false
      end
      if
        record.stream_event_id ~= nil
        and stream_event_id ~= ''
        and record.stream_event_id ~= stream_event_id
      then
        return false
      end
      return true
    end

    -- Exact fencing: the record must still carry a lease token and it must be
    -- the caller's. A missing current token is never permission.
    local function owns_lease(record, lease_token)
      if lease_token == nil or lease_token == '' then
        return false
      end
      if record.lease_token == nil then
        return false
      end
      return record.lease_token == lease_token
    end
    """;

  // ARGV: 1 stream_event_id, 2 lease_ms, 3 lease_token, 4 candidate_id,
  //       5 allow_rejected_reclaim, 6 allow_broker_submitting_reclaim,
  //       7 allow_recovery_adoption
  // Returns {code, raw_or_record, attempt}.
  public static readonly string Acquire = Prelude + """

    local stream_event_id = ARGV[1]
    local lease_ms = tonumber(ARGV[2])
    local lease_token = ARGV[3]
    local candidate_id = ARGV[4]
    local allow_rejected_reclaim = ARGV[5] == '1'
    local allow_broker_submitting_reclaim = ARGV[6] == '1'
    local allow_recovery_adoption = ARGV[7] == '1'
    local now = now_seconds()
    local raw = redis.call('GET', KEYS[1])
    local record = decode_record(raw)
    local attempt = 0
    local recovered_from = nil

    if record ~= nil then
      attempt = record.attempt or 0
      if record.state == '__invalid__' then
        return {5, raw or ''}
      end
      if not identity_matches(record, candidate_id, stream_event_id) then
        return {5, raw or ''}
      end
      if TERMINAL[record.state] then
        -- Flip coordination keys re-arm after a rejected claim; every other
        -- terminal record is immutable and only advances the stream cursor.
        if not (allow_rejected_reclaim and record.state == 'rejected') then
          return {3, raw or ''}
        end
      elseif RECOVERY[record.state] then
        if not allow_recovery_adoption then
          return {4, raw or ''}
        end
        if
          record.lease_expires_at ~= nil
          and record.lease_expires_at > now
        then
          return {2, raw or ''}
        end
        recovered_from = record.state
      elseif ACTIVE[record.state] then
        -- Legacy plain `processing` has no lease metadata and stays owned for
        -- as long as the Redis TTL holds.
        if record.legacy then
          return {2, raw or ''}
        end
        if
          record.lease_expires_at == nil
          or record.lease_expires_at > now
        then
          return {2, raw or ''}
        end
        if
          record.state == 'broker_submitting'
          and not allow_broker_submitting_reclaim
        then
          return {4, raw or ''}
        end
      elseif not RETRYABLE[record.state] then
        return {5, raw or ''}
      end
    end

    attempt = attempt + 1
    local claimed = encode_record({
      candidate_id = candidate_id,
      stream_event_id = (stream_event_id ~= '' and stream_event_id or nil),
      state = 'processing',
      lease_token = lease_token,
      lease_expires_at = now + math.floor(lease_ms / 1000),
      attempt = attempt,
      last_error = recovered_from,
      outcome = nil,
      updated_at = now,
      version = 1,
    })
    redis.call('SET', KEYS[1], claimed, 'PX', lease_ms)
    return {1, claimed, attempt}
    """;

  // ARGV: 1 stream_event_id, 2 lease_token, 3 lease_ms, 4 candidate_id
  public static readonly string Renew = Prelude + """

    local stream_event_id = ARGV[1]
    local lease_token = ARGV[2]
    local lease_ms = tonumber(ARGV[3])
    local candidate_id = ARGV[4]
    local now = now_seconds()
    local record = decode_record(redis.call('GET', KEYS[1]))
    if record == nil or record.state == '__invalid__' then
      return 0
    end
    if not identity_matches(record, candidate_id, stream_event_id) then
      return 0
    end
    if not owns_lease(record, lease_token) then
      return 0
    end
    if not ACTIVE[record.state] and not RECOVERY[record.state] then
      return 0
    end
    record.lease_expires_at = now + math.floor(lease_ms / 1000)
    record.updated_at = now
    redis.call('SET', KEYS[1], encode_record(record), 'PX', lease_ms)
    return 1
    """;

  // ARGV: 1 stream_event_id, 2 lease_token, 3 new_state, 4 candidate_id,
  //       5 lease_ms, 6 last_error
  public static readonly string Transition = Prelude + """

    local stream_event_id = ARGV[1]
    local lease_token = ARGV[2]
    local new_state = ARGV[3]
    local candidate_id = ARGV[4]
    local lease_ms = tonumber(ARGV[5])
    local last_error = nz(ARGV[6])
    local now = now_seconds()
    local record = decode_record(redis.call('GET', KEYS[1]))
    if record == nil or record.state == '__invalid__' then
      return 0
    end
    if not identity_matches(record, candidate_id, stream_event_id) then
      return 0
    end
    if TERMINAL[record.state] then
      return 0
    end
    if not owns_lease(record, lease_token) then
      return 0
    end
    local allowed = ALLOWED_TRANSITIONS[record.state]
    if allowed == nil or not allowed[new_state] then
      return 0
    end
    record.state = new_state
    record.updated_at = now
    record.last_error = last_error or record.last_error
    if new_state == 'retryable_error' then
      -- Releasing ownership: the next executor claims a fresh token.
      record.lease_token = nil
      record.lease_expires_at = nil
      redis.call('SET', KEYS[1], encode_record(record), 'EX', 604800)
      return 1
    end
    -- broker_submitting and broker_outcome_unknown stay lease-owned so the
    -- current executor keeps exclusive rights to finish or adopt.
    record.lease_expires_at = now + math.floor(lease_ms / 1000)
    redis.call('SET', KEYS[1], encode_record(record), 'EX', 604800)
    return 1
    """;

  // ARGV: 1 stream_event_id, 2 lease_token, 3 outcome, 4 candidate_id
  public static readonly string Complete = Prelude + """

    local stream_event_id = ARGV[1]
    local lease_token = ARGV[2]
    local outcome = ARGV[3]
    local candidate_id = ARGV[4]
    local now = now_seconds()
    local record = decode_record(redis.call('GET', KEYS[1]))
    if record == nil or record.state == '__invalid__' then
      return 0
    end
    if not identity_matches(record, candidate_id, stream_event_id) then
      return 0
    end
    if TERMINAL[record.state] then
      return 0
    end
    if not owns_lease(record, lease_token) then
      return 0
    end
    local state = 'completed'
    if string.sub(outcome, 1, 8) == 'ordered:' then
      state = 'ordered'
    elseif string.sub(outcome, 1, 9) == 'rejected:' then
      state = 'rejected'
    end
    local allowed = ALLOWED_COMPLETIONS[record.state]
    if allowed == nil or not allowed[state] then
      return 0
    end
    record.state = state
    record.outcome = outcome
    record.lease_token = nil
    record.lease_expires_at = nil
    record.updated_at = now
    redis.call('SET', KEYS[1], encode_record(record), 'EX', 604800)
    return 1
    """;

  // Administrative override for flip coordination keys only. Flip claims are
  // executor-internal rendezvous records with no owning lease after they turn
  // into `flip_pending:`; expiry and release must still be able to re-arm
  // them. Restricted to records whose outcome is a flip marker so it can never
  // rewrite a real broker outcome.
  // ARGV: 1 outcome, 2 candidate_id
  public static readonly string OverrideFlipClaim = Prelude + """

    local outcome = ARGV[1]
    local candidate_id = ARGV[2]
    local now = now_seconds()
    local raw = redis.call('GET', KEYS[1])
    local record = decode_record(raw)
    if record == nil then
      return 0
    end
    local current = record.outcome
    if current == nil or string.sub(current, 1, 13) ~= 'flip_pending:' then
      return 0
    end
    redis.call('SET', KEYS[1], encode_record({
      candidate_id = record.candidate_id or candidate_id,
      stream_event_id = record.stream_event_id,
      state = 'rejected',
      lease_token = nil,
      lease_expires_at = nil,
      attempt = record.attempt or 0,
      last_error = nil,
      outcome = outcome,
      updated_at = now,
      version = 1,
    }), 'EX', 604800)
    return 1
    """;
}

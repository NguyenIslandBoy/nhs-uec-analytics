{{ config(materialized='table') }}

-- Resolves each organisation to its terminal successor(s) by walking the succession graph.
--
-- Profiling established (docs/known-issues.md ODS-13, ODS-14):
--   * chains reach depth 3, so a self-join is insufficient
--   * 23 predecessors have multiple successors -- demergers, e.g. RAV -> RJ1, RJ2
--   * two predecessors (REX, REZ) split across the same successor pair, so the graph is
--     many-to-many in both directions
--
-- A predecessor with more than one terminal successor cannot have its historic activity
-- attributed without an arbitrary choice, so those rows are flagged rather than resolved.
-- MAX_DEPTH guards against cycles: none were observed, but an unguarded recursive CTE
-- against a future cyclic graph would not terminate.

{% set max_depth = 10 %}

with recursive walk (ods_code, successor_code, depth, path) as (

    select
        e.predecessor_code                              as ods_code,
        e.successor_code,
        1                                               as depth,
        [e.predecessor_code, e.successor_code]          as path
    from {{ ref('stg_ods__succession') }} as e

    union all

    select
        w.ods_code,
        e.successor_code,
        w.depth + 1                                     as depth,
        list_append(w.path, e.successor_code)           as path
    from walk as w
    inner join {{ ref('stg_ods__succession') }} as e
        on e.predecessor_code = w.successor_code
    where w.depth < {{ max_depth }}
      and not list_contains(w.path, e.successor_code)

),

terminal as (

    -- A successor is terminal when it is not itself a predecessor of anything.
    select
        w.ods_code,
        w.successor_code    as terminal_code,
        w.depth
    from walk as w
    left join {{ ref('stg_ods__succession') }} as onward
        on onward.predecessor_code = w.successor_code
    where onward.predecessor_code is null

),

deduped as (

    select
        ods_code,
        terminal_code,
        min(depth) as hops
    from terminal
    group by 1, 2

),

flagged as (

    select
        ods_code,
        terminal_code,
        hops,
        count(*) over (partition by ods_code) as terminal_count
    from deduped

)

select
    ods_code,
    terminal_code,
    hops,
    terminal_count,
    terminal_count > 1 as is_ambiguous
from flagged
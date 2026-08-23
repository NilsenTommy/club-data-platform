with matches as (

    select *
    from {{ source('silver', 'matches') }}

),

venues as (

    select *
    from {{ source('silver', 'venues') }}

),

weather_observations as (

    select *
    from {{ source('silver', 'weather_observations') }}
    where element in (
        'air_temperature',
        'sum(precipitation_amount PT1H)',
        'wind_speed'
    )

),

weather_candidates as (

    select distinct
        m.match_id,
        w.venue_id,
        m.kickoff_at,
        w.weather_station_id,
        w.observed_at,
        w.distance_to_venue_km
    from matches as m
    inner join weather_observations as w
        on m.venue_id = w.venue_id
       and abs(
            unix_timestamp(w.observed_at)
            - unix_timestamp(m.kickoff_at)
       ) <= 10800

),

ranked_weather_candidates as (

    select
        *,
        row_number() over (
            partition by match_id
            order by
                abs(
                    unix_timestamp(observed_at)
                    - unix_timestamp(kickoff_at)
                ),
                case
                    when observed_at > kickoff_at then 1
                    else 0
                end,
                observed_at,
                distance_to_venue_km asc nulls last,
                weather_station_id
        ) as weather_rank
    from weather_candidates

),

selected_weather as (

    select
        match_id,
        venue_id,
        weather_station_id,
        observed_at
    from ranked_weather_candidates
    where weather_rank = 1

),

weather_snapshots as (

    select
        selected.match_id,
        selected.observed_at as weather_observed_at,

        max(
            case
                when observations.element = 'air_temperature'
                    then observations.value
            end
        ) as temperature_c,

        max(
            case
                when observations.element =
                    'sum(precipitation_amount PT1H)'
                    then observations.value
            end
        ) as precipitation_mm,

        max(
            case
                when observations.element = 'wind_speed'
                    then observations.value
            end
        ) as wind_speed_ms

    from selected_weather as selected
    inner join weather_observations as observations
    on selected.venue_id = observations.venue_id
   and selected.weather_station_id = observations.weather_station_id
   and selected.observed_at = observations.observed_at

    group by
        selected.match_id,
        selected.observed_at

),

final as (

    select
        m.match_id,
        m.kickoff_at,
        m.competition,
        m.season,
        m.home_team_name,
        m.away_team_name,
        m.home_score,
        m.away_score,

        case
            when m.status not in ('complete', 'finished')
                or m.status is null
                or m.home_score is null
                or m.away_score is null
                then null

            when m.home_team_id = 293 and m.home_score > m.away_score
                then 'win'
            when m.home_team_id = 293 and m.home_score < m.away_score
                then 'loss'
            when m.home_team_id = 293
                then 'draw'

            when m.away_team_id = 293 and m.away_score > m.home_score
                then 'win'
            when m.away_team_id = 293 and m.away_score < m.home_score
                then 'loss'
            when m.away_team_id = 293
                then 'draw'

            else null
        end as result,

        m.venue_id,
        v.stadium_name,
        v.country,
        v.latitude,
        v.longitude,
        w.weather_observed_at,
        w.temperature_c,
        w.precipitation_mm,
        w.wind_speed_ms

    from matches as m

    left join venues as v
        on m.venue_id = v.venue_id

    left join weather_snapshots as w
        on m.match_id = w.match_id

)

select *
from final
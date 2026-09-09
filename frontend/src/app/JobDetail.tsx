import { Fragment, useState } from 'react';
import { LuMapPin } from 'react-icons/lu';
import { countryFlagUrl, inferredCountryName } from '../lib/countryInference';
import { salaryTier } from '../lib/salaryTier';
import {
  Accordion,
  Alert,
  Button,
  ButtonGroup,
  Card,
  Chip,
  Link,
  Separator,
  Skeleton,
  ScrollShadow,
  Typography,
} from '@heroui/react';
import {
  changeTimeline,
  contentCards,
  extractionStatusLabel,
  jobUrl,
  shouldOpenJob,
  uniqueSkillMentions,
} from '../lib/jobDetail';
import type { DetailState } from '../lib/useJobDetail';
import type { SimilarJobsState } from '../lib/similarJobs';
import type { Posting } from '../lib/workspace';
import {
  displayTime,
  displayValue,
  displayNumber,
  formatCount,
} from '../lib/workspace';
import { ApplicationTrend } from './ApplicationTrend';
import {
  presentRate,
  presentSummary,
  defaultPayPreferences,
  type PayPreferences,
  type FxRates,
} from '../lib/hourlyPay';

/** HeroUI compositions inside native responsive layout regions. */
export function JobDetailContent({
  state,
  preferences = defaultPayPreferences,
  fx = null,
  onNavigateJob,
  similarJobs = { status: 'idle' },
  postings = [],
}: {
  state: DetailState;
  preferences?: PayPreferences;
  fx?: FxRates | null;
  onNavigateJob?: (id: string) => void;
  similarJobs?: SimilarJobsState;
  postings?: readonly Posting[];
}) {
  const [historyOffset, setHistoryOffset] = useState(0);
  if (state.status === 'loading')
    return (
      <div
        className="posting-layout"
        role="status"
        aria-label="Loading stored job details"
        aria-busy="true"
      >
        {(['facts', 'description', 'similar'] as const).map((column) => (
          <section
            key={column}
            className={`posting-${column}`}
            aria-hidden="true"
          >
            <header className="posting-column-heading">
              {column === 'description' ? (
                <Card variant="transparent">
                  <Card.Header>
                    <div className="skeleton-company-line">
                      <Skeleton animationType="none">
                        <div className="skeleton-line-space" />
                      </Skeleton>
                    </div>
                    <div className="skeleton-title-block">
                      <Skeleton animationType="none">
                        <div className="skeleton-title-space" />
                      </Skeleton>
                      <div className="skeleton-short-title">
                        <Skeleton animationType="none">
                          <div className="skeleton-title-space" />
                        </Skeleton>
                      </div>
                    </div>
                  </Card.Header>
                  <Separator />
                  <Card.Footer>
                    <div className="skeleton-company-line">
                      <Skeleton animationType="none">
                        <div className="skeleton-line-space" />
                      </Skeleton>
                    </div>
                  </Card.Footer>
                </Card>
              ) : (
                <Typography type="h4">
                  {column === 'facts' ? 'At a glance' : 'Similar jobs'}
                </Typography>
              )}
            </header>
            <ScrollShadow hideScrollBar isEnabled={false}>
              <div className="posting-column-content">
                {[0, 1, 2].map((card) => (
                  <Card key={card}>
                    <Card.Header>
                      <div className="skeleton-short-title">
                        <Skeleton animationType="none">
                          <div className="skeleton-title-space" />
                        </Skeleton>
                      </div>
                    </Card.Header>
                    <Separator />
                    <Card.Content>
                      <div className="stack-small">
                        {Array.from(
                          {
                            length: column === 'description' ? 8 : 4,
                          },
                          (_, line) => (
                            <div
                              key={line}
                              className={
                                line % 4 === 3
                                  ? 'skeleton-company-line'
                                  : undefined
                              }
                            >
                              <Skeleton animationType="none">
                                <div className="skeleton-line-space" />
                              </Skeleton>
                            </div>
                          ),
                        )}
                      </div>
                    </Card.Content>
                  </Card>
                ))}
              </div>
            </ScrollShadow>
          </section>
        ))}
      </div>
    );
  if (state.status !== 'ready')
    return (
      <Alert status={state.status === 'not-found' ? 'warning' : 'danger'}>
        <Alert.Indicator />
        <Alert.Content>
          <Alert.Title>
            {state.status === 'not-found'
              ? 'Posting not found'
              : 'Job details unavailable'}
          </Alert.Title>
          <Alert.Description>
            {state.status === 'not-found'
              ? 'No posting with this ID is stored locally.'
              : 'Could not read this stored posting. No refresh or analysis was started.'}
          </Alert.Description>
        </Alert.Content>
      </Alert>
    );
  const {
    listing,
    content,
    content_observed_at: observed,
    country_inference: countryInference,
    analysis,
    history,
  } = state.data;
  const sections = content ? contentCards(content.fields) : [];
  const skills = uniqueSkillMentions(analysis.skills);
  const arrangement = ['Remote', 'Hybrid', 'In-person'].find(
    (value) =>
      value === content?.fields['Employment Location Arrangement:']?.trim(),
  );
  const location = [
    analysis.city?.value ?? content?.fields['Job - City:'],
    analysis.province?.value ?? content?.fields['Job - Province/State:'],
    analysis.country?.value ?? content?.fields['Job - Country:'],
  ]
    .filter(Boolean)
    .join(', ');
  const timeline = changeTimeline(history.observations, (item) =>
    JSON.stringify([
      item.application_count,
      item.application_count_raw,
      item.openings,
      item.openings_raw,
      item.deadline,
    ]),
  );
  const index = Math.max(0, timeline.length - 1 - historyOffset);
  const observation = timeline[index];
  const externalStatus =
    analysis.external_application_required === true
      ? 'External application required'
      : analysis.external_application_required === false
        ? 'No required external application found'
        : 'External application requirement unknown';

  return (
    <div className="posting-layout">
      <aside className="posting-facts" aria-label="At a glance">
        <header className="posting-column-heading">
          <Typography type="h4">At a glance</Typography>
        </header>
        <ScrollShadow
          hideScrollBar
          tabIndex={0}
          aria-label="At a glance scroll area"
        >
          <div className="posting-column-content">
            <Card>
              <Card.Header>
                <Card.Title>{displayValue(listing.organization)}</Card.Title>
              </Card.Header>
              <Separator />
              <Card.Content>
                <div className="stack-small">
                  <div className="detail-chip-list">
                    {analysis.levels.map((level) => (
                      <Chip size="sm" key={level}>
                        {level}
                      </Chip>
                    ))}
                    {analysis.work_term?.value && (
                      <Chip size="sm">{analysis.work_term.value}</Chip>
                    )}
                    {analysis.duration?.value && (
                      <Chip size="sm">{analysis.duration.value}</Chip>
                    )}
                  </div>
                  <dl className="posting-fact-list posting-counts">
                    <div className="posting-field-wide">
                      <dt>
                        <Typography type="body-xs" color="muted">
                          Location
                        </Typography>
                      </dt>
                      <dd>
                        <div className="location-with-arrangement">
                          <div className="location-field">
                            <LuMapPin
                              size={16}
                              color="var(--muted)"
                              aria-hidden="true"
                            />
                            {inferredCountryName(countryInference) && (
                              <img
                                className="country-flag"
                                src={countryFlagUrl(
                                  countryInference.country_code,
                                )}
                                alt={countryInference.country_name ?? ''}
                                width={16}
                              />
                            )}
                            <Typography type="body-sm">
                              {displayValue(location || null)}
                            </Typography>
                          </div>
                          {arrangement && (
                            <Chip size="sm" variant="soft">
                              {arrangement}
                            </Chip>
                          )}
                        </div>
                      </dd>
                    </div>
                    <div>
                      <dt>
                        <Typography type="body-xs" color="muted">
                          Openings
                        </Typography>
                      </dt>
                      <dd>
                        <Typography type="body" weight="semibold">
                          {displayNumber(listing.openings)}
                        </Typography>
                      </dd>
                    </div>
                    <div>
                      <dt>
                        <Typography type="body-xs" color="muted">
                          Applications
                        </Typography>
                      </dt>
                      <dd>
                        <Typography type="body" weight="semibold">
                          {displayNumber(listing.application_count)}
                        </Typography>
                      </dd>
                    </div>
                    <div className="posting-field-wide">
                      <dt>
                        <Typography type="body-xs" color="muted">
                          Deadline
                        </Typography>
                      </dt>
                      <dd>
                        <Typography type="body-sm" weight="medium">
                          {displayValue(listing.deadline)}
                        </Typography>
                      </dd>
                    </div>
                  </dl>
                  <Typography type="body-xs" color="muted">
                    Posting {state.id}
                  </Typography>
                </div>
              </Card.Content>
            </Card>
            <Card>
              <Card.Header>
                <Card.Title>Compensation</Card.Title>
              </Card.Header>
              <Separator />
              <Card.Content>
                <div className="stack">
                  <Typography type="body-xs" color="muted">
                    {analysis.salary_extraction
                      ? extractionStatusLabel(analysis.salary_extraction.state)
                      : 'Saved extraction status unavailable from this API.'}
                  </Typography>
                  {analysis.compensation.some(
                    (rate) => rate.currency === null,
                  ) && (
                    <Typography type="body-xs" color="muted">
                      * Currency assumed to be CAD; retained after conversion.
                    </Typography>
                  )}
                  {analysis.compensation_needs_review && (
                    <Typography type="body-sm">
                      Conflicting rate evidence needs review. Disputed rates and
                      aggregate sorting are withheld; see the saved source.
                    </Typography>
                  )}
                  {analysis.compensation.length ? (
                    analysis.compensation.map((rate, i) => {
                      const display = presentRate(rate, preferences, fx);
                      return (
                        <Fragment key={i}>
                          {i > 0 && <Separator />}
                          <div className="stack-small">
                            <Typography type="h6">
                              <span
                                className={`salary-value salary-${salaryTier(analysis.compensation, fx, analysis.compensation_needs_review)}`}
                              >
                                {display.label}
                              </span>
                            </Typography>
                            {rate.eligibility && (
                              <div>
                                <Chip size="sm" variant="soft" color="accent">
                                  {rate.eligibility}
                                </Chip>
                              </div>
                            )}
                            <Typography type="body-xs" color="muted">
                              Source: {rate.evidence_text}
                            </Typography>
                          </div>
                        </Fragment>
                      );
                    })
                  ) : (
                    <Typography type="body-sm" color="muted">
                      No verified rate. See the saved compensation details.
                    </Typography>
                  )}
                </div>
              </Card.Content>
            </Card>
            <ApplicationTrend observations={history.observations} />
            {(skills.length > 0 || analysis.keyword_extraction) && (
              <Card>
                <Card.Header>
                  <Card.Title>Keywords and skills</Card.Title>
                </Card.Header>
                <Separator />
                <Card.Content>
                  <Typography type="body-sm" weight="semibold">
                    Extracted keywords
                  </Typography>
                  <Typography type="body-xs" color="muted">
                    {analysis.keyword_extraction
                      ? extractionStatusLabel(analysis.keyword_extraction.state)
                      : 'Saved extraction status unavailable from this API.'}{' '}
                    Keywords are source mentions, not applicant requirements.
                    Coverage may be incomplete.
                  </Typography>
                  <div className="detail-chip-list">
                    {(analysis.keywords ?? []).map((keyword) => (
                      <Chip size="sm" key={keyword.name}>
                        {keyword.name}
                      </Chip>
                    ))}
                  </div>
                  {skills.length > 0 && (
                    <Typography type="body-sm" weight="semibold">
                      Validated skill mentions
                    </Typography>
                  )}
                  <div className="detail-chip-list">
                    {skills.map((skill) => (
                      <Chip size="sm" key={skill.name}>
                        {skill.name}
                      </Chip>
                    ))}
                  </div>
                </Card.Content>
              </Card>
            )}
            <Card>
              <Card.Header>
                <Card.Title>Application route</Card.Title>
              </Card.Header>
              <Separator />
              <Card.Content>
                <div className="stack-small">
                  <Typography
                    type="body-sm"
                    color={
                      analysis.external_application_required === true
                        ? undefined
                        : 'muted'
                    }
                  >
                    {externalStatus}
                  </Typography>
                  <Link
                    href={analysis.waterlooworks_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    WaterlooWorks job board
                    <Link.Icon />
                  </Link>
                  {analysis.employer_links.map((link) => (
                    <div
                      className="stack-small"
                      key={`${link.url}:${link.classification}`}
                    >
                      <Typography type="body-xs" color="muted">
                        {link.classification}
                      </Typography>
                      <Link
                        href={link.url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Employer application link
                        <Link.Icon />
                      </Link>
                    </div>
                  ))}
                </div>
              </Card.Content>
            </Card>
            {timeline.length > 1 && observation && (
              <Card>
                <Card.Header>
                  <Card.Title>Application history</Card.Title>
                </Card.Header>
                <Separator />
                <Card.Content>
                  <div className="stack-small">
                    <div className="timeline-controls">
                      <ButtonGroup
                        size="sm"
                        variant="tertiary"
                        aria-label="Application timeline"
                      >
                        <Button
                          isIconOnly
                          aria-label="Previous change"
                          isDisabled={index === 0}
                          onPress={() =>
                            setHistoryOffset(timeline.length - index)
                          }
                        >
                          {'<'}
                        </Button>
                        <Button
                          isIconOnly
                          aria-label="Next change"
                          isDisabled={index === timeline.length - 1}
                          onPress={() =>
                            setHistoryOffset(
                              Math.max(0, timeline.length - index - 2),
                            )
                          }
                        >
                          {'>'}
                        </Button>
                      </ButtonGroup>
                      <Typography type="body-xs" color="muted">
                        {displayNumber(index + 1)} /{' '}
                        {displayNumber(timeline.length)}
                      </Typography>
                    </div>
                    <div aria-live="polite" className="stack-small">
                      <Typography type="body-sm">
                        {displayTime(observation.observed_at)}
                      </Typography>
                      <Typography type="body-sm">
                        Applications:{' '}
                        {displayNumber(
                          observation.application_count ??
                            observation.application_count_raw,
                        )}
                      </Typography>
                      <Typography type="body-sm">
                        Openings:{' '}
                        {displayNumber(
                          observation.openings ?? observation.openings_raw,
                        )}
                      </Typography>
                      <Typography type="body-sm">
                        Deadline: {displayValue(observation.deadline)}
                      </Typography>
                      <Typography type="body-xs" color="muted">
                        {observation.timestamp_provenance.replaceAll('_', ' ')}{' '}
                        · unchanged observations skipped
                      </Typography>
                    </div>
                  </div>
                </Card.Content>
              </Card>
            )}
            <Accordion>
              <Accordion.Item>
                <Accordion.Heading>
                  <Accordion.Trigger>
                    Analysis status
                    <Accordion.Indicator />
                  </Accordion.Trigger>
                </Accordion.Heading>
                <Accordion.Panel>
                  <Accordion.Body>
                    <div className="stack-small">
                      <Typography type="body-sm">
                        {analysis.status === 'available'
                          ? 'Current source-backed facts.'
                          : analysis.unavailable_reason === 'stale_projection'
                            ? 'Saved source changed after analysis. Older derived output is hidden.'
                            : 'This posting has not been normalized.'}
                      </Typography>
                      <Typography type="body-sm">
                        Salary:{' '}
                        {analysis.salary_extraction
                          ? extractionStatusLabel(
                              analysis.salary_extraction.state,
                            )
                          : 'Saved extraction status unavailable from this API.'}
                      </Typography>
                      <Typography type="body-sm">
                        Keywords:{' '}
                        {analysis.keyword_extraction
                          ? extractionStatusLabel(
                              analysis.keyword_extraction.state,
                            )
                          : 'Saved extraction status unavailable from this API.'}
                      </Typography>
                      <Typography type="body-xs" color="muted">
                        Viewing this page does not run extraction. Accepted
                        output does not guarantee completeness.
                        {analysis.model_producer &&
                          ` Legacy validated model: ${analysis.model_producer}.`}
                      </Typography>
                      {analysis.model_error_category && (
                        <Typography type="body-xs" color="muted">
                          {analysis.model_error_category}
                        </Typography>
                      )}
                    </div>
                  </Accordion.Body>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>
          </div>
        </ScrollShadow>
      </aside>

      <section className="posting-description" aria-label="Posting description">
        <header className="posting-column-heading">
          <Card variant="transparent">
            <Card.Header>
              <div className="posting-identity-line">
                <Typography type="body-sm" weight="medium" color="muted">
                  {displayValue(listing.organization)}
                </Typography>
                <Chip size="sm" variant="soft" color="accent">
                  #{state.id}
                </Chip>
              </div>
              <Typography type="h3">{displayValue(listing.title)}</Typography>
            </Card.Header>
            <Separator />
            <Card.Footer>
              <Typography type="body-xs" color="muted">
                Saved description ·{' '}
                {observed ? displayTime(observed) : 'not collected'}
              </Typography>
            </Card.Footer>
          </Card>
        </header>
        <ScrollShadow
          hideScrollBar
          tabIndex={0}
          aria-label="Posting description scroll area"
        >
          <div className="posting-column-content">
            {content === null ? (
              <Alert status="warning">
                <Alert.Indicator />
                <Alert.Content>
                  <Alert.Title>Detail content not collected</Alert.Title>
                  <Alert.Description>
                    Opening this page does not fetch WaterlooWorks.
                  </Alert.Description>
                </Alert.Content>
              </Alert>
            ) : (
              sections.map((section) => (
                <Card key={section.title}>
                  <Card.Header>
                    <Card.Title>{section.title}</Card.Title>
                  </Card.Header>
                  <Separator />
                  <Card.Content>
                    {section.fields.length === 1 &&
                    section.title === section.fields[0][0].replace(/:$/, '') ? (
                      <div className="detail-value">
                        <Typography>{section.fields[0][1]}</Typography>
                      </div>
                    ) : (
                      <dl className="posting-source-facts">
                        {section.fields.map(([label, text]) => (
                          <div
                            key={label}
                            className={
                              text.length > 180
                                ? 'posting-field-wide'
                                : undefined
                            }
                          >
                            <dt>
                              <Typography type="body-xs" color="muted">
                                {label.replace(/:$/, '')}
                              </Typography>
                            </dt>
                            <dd>
                              <Typography type="body-sm">{text}</Typography>
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </Card.Content>
                </Card>
              ))
            )}
          </div>
        </ScrollShadow>
      </section>

      <aside
        className="posting-similar"
        aria-label="Similar jobs"
        aria-busy={similarJobs.status === 'loading'}
      >
        <header className="posting-column-heading">
          <Typography type="h4">Similar jobs</Typography>
          <Typography type="body-xs" color="muted" role="status">
            {similarJobs.status === 'ready'
              ? `${displayNumber(similarJobs.result.positive)} matches · showing ${similarJobs.result.items.length} · ${displayNumber(similarJobs.result.indexed)} / ${displayNumber(similarJobs.result.total)} indexed`
              : 'Comparing saved keyword indexes'}
          </Typography>
          <Typography type="body-xs" color="muted">
            Weighted keyword coverage—not eligibility.
          </Typography>
        </header>
        <ScrollShadow
          hideScrollBar
          tabIndex={0}
          aria-label="Similar jobs scroll area"
        >
          <div className="posting-column-content">
            {similarJobs.status === 'ready' &&
            similarJobs.result.items.length ? (
              similarJobs.result.items.map((item) => {
                const posting = postings.find(
                  (p) => p.posting_id === item.posting_id,
                );
                return (
                  <Card
                    key={item.posting_id}
                    data-similar-posting={item.posting_id}
                  >
                    <Card.Header>
                      <Card.Title>
                        <Link
                          href={jobUrl(window.location.href, item.posting_id)}
                          onClick={(event) => {
                            if (!onNavigateJob || !shouldOpenJob(event)) return;
                            event.preventDefault();
                            onNavigateJob(item.posting_id);
                          }}
                        >
                          {posting
                            ? displayValue(posting.listing.title)
                            : `#${item.posting_id}`}
                        </Link>
                      </Card.Title>
                      <Card.Description>
                        {displayValue(posting?.listing.organization)}
                      </Card.Description>
                    </Card.Header>
                    <Separator />
                    <Card.Content>
                      <div className="stack-small">
                        {item.partial && (
                          <div className="detail-chip-list">
                            <Chip size="sm" variant="soft">
                              Partial index
                            </Chip>
                          </div>
                        )}
                        <Typography type="body-sm">
                          {formatCount(
                            posting?.listing.application_count,
                            'application',
                          )}{' '}
                          · {formatCount(posting?.listing.openings, 'opening')}
                        </Typography>
                        <div className="location-with-arrangement">
                          <div className="location-field">
                            <LuMapPin
                              size={16}
                              color="var(--muted)"
                              role="img"
                              aria-label="Location"
                            />
                            {inferredCountryName(
                              posting?.country_inference,
                            ) && (
                              <img
                                className="country-flag"
                                src={countryFlagUrl(
                                  posting?.country_inference.country_code,
                                )}
                                alt={
                                  posting?.country_inference.country_name ?? ''
                                }
                                width={16}
                              />
                            )}
                            <Typography type="body-sm" color="muted">
                              {displayValue(posting?.location)}
                            </Typography>
                          </div>
                          {posting?.work_arrangement && (
                            <Chip size="sm" variant="soft">
                              {posting.work_arrangement}
                            </Chip>
                          )}
                        </div>
                        <Typography type="body-sm">
                          <span
                            className={`salary-value salary-${salaryTier(posting?.compensation_sort?.source_rates ?? [], fx, posting?.compensation_sort?.needs_review)}`}
                          >
                            {
                              presentSummary(
                                posting?.compensation_sort ?? null,
                                preferences,
                                fx,
                              ).label
                            }
                          </span>
                        </Typography>
                      </div>
                    </Card.Content>
                  </Card>
                );
              })
            ) : (
              <Card>
                <Card.Header>
                  <Card.Title>
                    {similarJobs.status === 'loading'
                      ? 'Finding similar jobs…'
                      : similarJobs.status === 'error'
                        ? 'Similar jobs unavailable'
                        : 'No keyword matches yet'}
                  </Card.Title>
                </Card.Header>
                <Separator />
                <Card.Content>
                  <Typography color="muted">
                    {similarJobs.status === 'loading'
                      ? 'Preparing saved keyword weights in the background.'
                      : similarJobs.status === 'error'
                        ? 'Could not read the current keyword index. No refresh or AI extraction was started.'
                        : similarJobs.status === 'idle'
                          ? 'A saved description is needed for comparison.'
                          : 'No positive coverage matches in the available source-current indexes.'}
                  </Typography>
                </Card.Content>
              </Card>
            )}
          </div>
        </ScrollShadow>
      </aside>
    </div>
  );
}

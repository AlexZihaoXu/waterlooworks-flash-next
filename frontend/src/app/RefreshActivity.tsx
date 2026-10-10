import { memo, useMemo, useState, type ReactNode } from 'react';
import {
  Button,
  Card,
  Disclosure,
  Label,
  ProgressBar,
  Typography,
} from '@heroui/react';
import { LuTerminal } from 'react-icons/lu';
import { RefreshLogEntry } from './RefreshLogEntry';
import { RefreshPanel } from './RefreshPanel';
import { refreshProgress, refreshStageLabel } from '../lib/refreshProgress';
import { refreshRunning } from '../lib/refreshState';
import { displayNumber, type RefreshSnapshot } from '../lib/workspace';
import { useLogFollow } from '../lib/useLogFollow';
import {
  useDetailEta,
  useRefreshEta,
  useRefreshLogVisibility,
} from '../lib/useRefreshPresentation';

// Existing HeroUI console composition; the trigger has no Button styling.
export const RefreshConsole = memo(function RefreshConsole({
  entries,
  status,
  revision,
}: {
  entries: readonly { key: string; content: ReactNode }[];
  status: string;
  revision: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const { ref, following, onScroll, jumpToLatest } = useLogFollow(
    expanded,
    `${revision}:${expanded}`,
  );
  return (
    <Card variant="secondary">
      <Card.Content>
        <Disclosure isExpanded={expanded} onExpandedChange={setExpanded}>
          <Disclosure.Heading>
            <span className="log-heading-layout">
              <Disclosure.Trigger
                aria-label={
                  expanded ? 'Collapse refresh log' : 'Expand refresh log'
                }
              >
                <span className="log-preview">
                  <span className="filter-heading">
                    <LuTerminal aria-hidden="true" />
                    <Typography type="body-xs">
                      Refresh log · {status}
                    </Typography>
                    <Disclosure.Indicator />
                  </span>
                  {!expanded && (
                    <span className="log-preview-lines">
                      {entries.length ? (
                        entries.slice(-3).map((entry) => (
                          <span className="log-preview-line" key={entry.key}>
                            {entry.content}
                          </span>
                        ))
                      ) : (
                        <Typography type="code">
                          Waiting for refresh progress.
                        </Typography>
                      )}
                    </span>
                  )}
                </span>
              </Disclosure.Trigger>
            </span>
          </Disclosure.Heading>
          <Disclosure.Content>
            <Disclosure.Body>
              <div className="stack-small">
                <div className="workspace-toolbar">
                  <Typography type="body-xs" color="muted">
                    {following ? 'Following latest' : 'Scroll paused'}
                  </Typography>
                  {!following && (
                    <Button size="sm" variant="tertiary" onPress={jumpToLatest}>
                      Jump to latest
                    </Button>
                  )}
                </div>
                <div
                  ref={ref}
                  onScroll={onScroll}
                  className="log-window"
                  role="log"
                  aria-label="Refresh log"
                  aria-live="polite"
                  aria-relevant="additions"
                  tabIndex={0}
                >
                  {entries.length ? (
                    entries.map((entry) => (
                      <div className="console-line" key={entry.key}>
                        {entry.content}
                      </div>
                    ))
                  ) : (
                    <Typography type="code">
                      Waiting for refresh progress.
                    </Typography>
                  )}
                </div>
              </div>
            </Disclosure.Body>
          </Disclosure.Content>
        </Disclosure>
      </Card.Content>
    </Card>
  );
});

export function RefreshActivity({
  refresh,
}: {
  refresh: RefreshSnapshot | null;
}) {
  const running = refreshRunning(refresh);
  const runId = refresh?.run_id ?? null;
  const eta = useRefreshEta(
    runId,
    running,
    refresh?.eta_seconds ?? null,
    refresh?.pages_completed ?? 0,
  );
  const detailEta = useDetailEta(
    runId,
    running,
    refresh?.stage ?? '',
    refresh?.details_completed ?? 0,
    refresh?.details_total ?? 0,
  );
  const visible = useRefreshLogVisibility(runId, running);
  const entries = useMemo(
    () =>
      refresh?.logs.map((log) => ({
        key: `${runId}-${log.sequence}`,
        content: (
          <RefreshLogEntry
            time={new Date(log.at).toLocaleTimeString('en-GB')}
            level={log.level}
            message={log.message}
          />
        ),
      })) ?? [],
    [refresh?.logs, runId],
  );
  if (!refresh) return null;
  const progress = refreshProgress(
    refresh.stage,
    [refresh.pages_completed, refresh.total_pages],
    [refresh.details_completed, refresh.details_total],
    [refresh.analysis_completed ?? 0, refresh.analysis_total ?? 0],
    refresh.analysis_provider,
  );
  const { detailsPhase } = progress;
  const analysisPhase =
    refresh.stage === 'analysis' || refresh.stage === 'normalizing';
  const displayedEta = analysisPhase ? null : detailsPhase ? detailEta : eta;
  const etaLabel =
    detailsPhase && detailEta === null
      ? refresh.details_total > 0 &&
        refresh.details_completed >= refresh.details_total
        ? 'Details complete'
        : 'Estimating ETA…'
      : displayedEta === null
        ? 'ETA unavailable'
        : displayedEta === 0
          ? 'Updating ETA…'
          : `ETA ~${Math.floor(displayedEta / 60)}m ${displayedEta % 60}s`;
  return (
    <>
      <RefreshPanel visible={running}>
        <div className="stack-small">
          <ProgressBar
            aria-label={progress.label}
            value={progress.value}
            maxValue={progress.maxValue}
            isIndeterminate={progress.isIndeterminate}
          >
            <Label>
              {progress.stageLabel} · {etaLabel}
            </Label>
            <ProgressBar.Output />
            <ProgressBar.Track>
              <ProgressBar.Fill />
            </ProgressBar.Track>
          </ProgressBar>
          <Typography type="body-sm" color="muted" role="status">
            {analysisPhase ? (
              <>
                {displayNumber(refresh.analysis_completed ?? 0)} /{' '}
                {displayNumber(refresh.analysis_total ?? 0)} processed ·{' '}
                {displayNumber(refresh.analysis_accepted ?? 0)} accepted ·{' '}
                {displayNumber(refresh.analysis_rejected ?? 0)} rejected ·{' '}
                {displayNumber(refresh.analysis_failed ?? 0)} failed ·{' '}
                {displayNumber(refresh.analysis_skipped ?? 0)} skipped ·{' '}
                {displayNumber(refresh.analysis_cache_hits ?? 0)} cache hits
              </>
            ) : detailsPhase ? (
              <>
                {displayNumber(refresh.details_completed)} /{' '}
                {displayNumber(refresh.details_total)} detail contents ·{' '}
                {displayNumber(refresh.detail_requests)} requests ·{' '}
                {displayNumber(refresh.detail_retries)} retries ·{' '}
                {displayNumber(refresh.detail_bursts)} bursts
                {refresh.detail_wait_seconds > 0 &&
                  ` · Scheduled wait ${refresh.detail_wait_seconds.toFixed(1)}s`}
                {refresh.detail_limited && ' · Server verification cap'}
              </>
            ) : (
              <>
                {displayNumber(refresh.pages_completed)} /{' '}
                {displayNumber(refresh.total_pages ?? 'unknown')} pages ·{' '}
                {displayNumber(refresh.unique_postings)} postings ·{' '}
                {displayNumber(refresh.records_received)} records ·{' '}
                {displayNumber(refresh.duplicates)} duplicates
              </>
            )}
          </Typography>
        </div>
      </RefreshPanel>
      <RefreshPanel visible={visible}>
        <RefreshConsole
          key={runId}
          entries={entries}
          status={`${refresh.status} · ${refreshStageLabel(refresh.stage, refresh.analysis_provider)}`}
          revision={`${runId}:${refresh.logs.at(-1)?.sequence ?? 0}`}
        />
      </RefreshPanel>
    </>
  );
}

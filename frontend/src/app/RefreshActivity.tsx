import { memo, useMemo, useState, type ReactNode } from 'react';
import {
  Button,
  Card,
  Chip,
  Disclosure,
  Drawer,
  Label,
  ProgressBar,
  Typography,
} from '@heroui/react';
import { LuChevronUp, LuTerminal } from 'react-icons/lu';
import { logPresentation } from '../lib/logPresentation';
import { refreshShortStatus, refreshStats } from '../lib/refreshSummary';
import { useCompactLayout } from '../lib/useCompactLayout';
import { RefreshLogEntry } from './RefreshLogEntry';
import { RefreshPanel } from './RefreshPanel';
import {
  refreshProgress,
  refreshStageLabel,
  refreshStopLabel,
} from '../lib/refreshProgress';
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

const LOG_STEP = 20;

type Progress = ReturnType<typeof refreshProgress>;

// Phones: one HeroUI ProgressBar row; details open in a documented bottom
// Drawer with wrapped, newest-first log text instead of a truncated console.
function CompactRefreshStatus({
  refresh,
  running,
  progress,
  etaLabel,
}: {
  refresh: RefreshSnapshot;
  running: boolean;
  progress: Progress;
  etaLabel: string;
}) {
  const [limit, setLimit] = useState(LOG_STEP);
  const stageLabel = refreshStageLabel(
    refresh.stage,
    refresh.analysis_provider,
  );
  const stop = refreshStopLabel(refresh.pacing_stop);
  const shown = refresh.logs.slice(-limit).reverse();
  const older = refresh.logs.length - shown.length;
  return (
    <div className="refresh-status-row">
      {running ? (
        <ProgressBar
          aria-label={progress.label}
          size="sm"
          value={progress.value}
          maxValue={progress.maxValue}
          isIndeterminate={progress.isIndeterminate}
        >
          <Label>{refreshShortStatus(refresh)}</Label>
          <ProgressBar.Output />
          <ProgressBar.Track>
            <ProgressBar.Fill />
          </ProgressBar.Track>
        </ProgressBar>
      ) : (
        <Typography type="body-sm" color="muted" truncate role="status">
          Refresh · {refreshStageLabel(refresh.stage)}
          {stop ? ' · stopped early' : ''}
        </Typography>
      )}
      <Drawer>
        <Button
          isIconOnly
          size="lg"
          variant="tertiary"
          aria-label="Refresh details"
          aria-haspopup="dialog"
        >
          <LuChevronUp aria-hidden="true" />
        </Button>
        <Drawer.Backdrop>
          <Drawer.Content placement="bottom">
            <Drawer.Dialog>
              <Drawer.Handle />
              <Drawer.CloseTrigger />
              <Drawer.Header>
                <Drawer.Heading>Refresh progress</Drawer.Heading>
              </Drawer.Header>
              <Drawer.Body>
                <div className="stack refresh-details">
                  <div className="stack-small">
                    <Typography type="body-sm" weight="medium">
                      {stageLabel}
                    </Typography>
                    {running && (
                      <Typography type="body-xs" color="muted">
                        {etaLabel}
                      </Typography>
                    )}
                    {stop && (
                      <Typography type="body-xs" color="muted">
                        {stop}
                      </Typography>
                    )}
                  </div>
                  {running && (
                    <ProgressBar
                      aria-label={progress.label}
                      value={progress.value}
                      maxValue={progress.maxValue}
                      isIndeterminate={progress.isIndeterminate}
                    >
                      <ProgressBar.Output />
                      <ProgressBar.Track>
                        <ProgressBar.Fill />
                      </ProgressBar.Track>
                    </ProgressBar>
                  )}
                  <ul className="refresh-stats" aria-label="Refresh counters">
                    {refreshStats(refresh).map((stat) => (
                      <li key={stat.label}>
                        <div className="refresh-stat">
                          <Typography type="body-xs" color="muted">
                            {stat.label}
                          </Typography>
                          <Typography type="body-sm" weight="semibold">
                            {stat.value}
                          </Typography>
                        </div>
                      </li>
                    ))}
                  </ul>
                  <div className="stack-small">
                    <Typography type="h6" render={(props) => <h3 {...props} />}>
                      Log · newest first
                    </Typography>
                    <div
                      role="log"
                      aria-label="Refresh log"
                      aria-live="polite"
                      aria-relevant="additions"
                    >
                      {shown.length ? (
                        <ol className="refresh-log-list">
                          {shown.map((log) => {
                            const style = logPresentation[log.level];
                            return (
                              <li
                                key={`${refresh.run_id}-${log.sequence}`}
                                className="refresh-log-item"
                              >
                                <span className="refresh-log-meta">
                                  <Typography type="body-xs" color="muted">
                                    {new Date(log.at).toLocaleTimeString(
                                      'en-GB',
                                    )}
                                  </Typography>
                                  {style.muted ? (
                                    <Typography type="body-xs" color="muted">
                                      {log.level.toUpperCase()}
                                    </Typography>
                                  ) : (
                                    <Chip
                                      size="sm"
                                      variant={style.variant}
                                      color={style.color}
                                    >
                                      {log.level.toUpperCase()}
                                    </Chip>
                                  )}
                                </span>
                                <span className="refresh-log-message">
                                  <Typography
                                    type="body-sm"
                                    color={style.muted ? 'muted' : 'default'}
                                  >
                                    {log.message}
                                  </Typography>
                                </span>
                              </li>
                            );
                          })}
                        </ol>
                      ) : (
                        <Typography type="body-sm" color="muted">
                          Waiting for refresh progress.
                        </Typography>
                      )}
                    </div>
                    {older > 0 && (
                      <Button
                        fullWidth
                        size="lg"
                        variant="tertiary"
                        onPress={() => setLimit((value) => value + LOG_STEP)}
                      >
                        Show {Math.min(older, LOG_STEP)} older entries
                      </Button>
                    )}
                  </div>
                </div>
              </Drawer.Body>
            </Drawer.Dialog>
          </Drawer.Content>
        </Drawer.Backdrop>
      </Drawer>
    </div>
  );
}

export function RefreshActivity({
  refresh,
}: {
  refresh: RefreshSnapshot | null;
}) {
  const compact = useCompactLayout();
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
  if (compact)
    return (
      <RefreshPanel visible={visible}>
        <CompactRefreshStatus
          key={runId}
          refresh={refresh}
          running={running}
          progress={progress}
          etaLabel={etaLabel}
        />
      </RefreshPanel>
    );
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
                {displayNumber(refresh.detail_bursts)} rest breaks
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
                {refresh.detail_wait_seconds > 0 &&
                  ` · Scheduled wait ${refresh.detail_wait_seconds.toFixed(1)}s`}
              </>
            )}
            {!analysisPhase &&
              ` · ${displayNumber(refresh.ww_requests ?? 0)}${
                refresh.ww_request_budget != null
                  ? ` / ${displayNumber(refresh.ww_request_budget)}`
                  : ''
              } WaterlooWorks requests`}
          </Typography>
        </div>
      </RefreshPanel>
      <RefreshPanel visible={visible}>
        <RefreshConsole
          key={runId}
          entries={entries}
          status={[
            refresh.status,
            refreshStageLabel(refresh.stage, refresh.analysis_provider),
            refreshStopLabel(refresh.pacing_stop),
          ]
            .filter(Boolean)
            .join(' · ')}
          revision={`${runId}:${refresh.logs.at(-1)?.sequence ?? 0}`}
        />
      </RefreshPanel>
    </>
  );
}

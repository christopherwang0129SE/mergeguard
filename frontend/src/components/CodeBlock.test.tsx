import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { CodeBlock } from './CodeBlock';
import { TruthTable } from './TruthTable';
import { DocsSources } from './DocsSources';

describe('CodeBlock', () => {
  it('is a named, focusable region so long lines scroll inside it', () => {
    render(<CodeBlock code={'a = 1\n'} label="Generated test code" />);
    const region = screen.getByRole('region', { name: 'Generated test code' });
    expect(region.tagName).toBe('PRE');
    expect(region).toHaveAttribute('tabindex', '0');
  });

  it('keeps the code text exact, line numbers come from CSS', () => {
    const code = 'import x\n\ndef test():\n    assert x == 1\n';
    const { container } = render(<CodeBlock code={code} label="c" numbered />);
    expect(container.querySelector('code')?.textContent).toBe(code);
    expect(container.querySelector('pre')).toHaveAttribute('data-numbered', 'true');
    expect(container.querySelectorAll('.code-line')).toHaveLength(4);
  });

  it('marks the given lines', () => {
    const { container } = render(<CodeBlock code={'a\nb\nc'} label="c" marked={[2]} />);
    const lines = container.querySelectorAll('.code-line');
    expect([...lines].map((l) => l.hasAttribute('data-marked'))).toEqual([false, true, false]);
  });

  it('marks pytest failure lines when asked', () => {
    const { container } = render(
      <CodeBlock code={'    x = 1\n>   assert x == 2\nE   assert 1 == 2\nt.py:2: AssertionError'} label="c" markFailures />
    );
    const marked = [...container.querySelectorAll('.code-line')].map((l) => l.hasAttribute('data-marked'));
    expect(marked).toEqual([false, true, true, false]);
  });
});

describe('TruthTable', () => {
  const run = (target: string, ref: string, passed: number, failed: number, extra = {}) => ({
    target, ref, passed, failed, errors: 0, timed_out: false, ...extra,
  });
  const props = { branchA: 'feat/a', branchB: 'feat/b', label: 'Test results per checkout' };

  it('lays the four checkouts out as base, B / A, merge', () => {
    render(
      <TruthTable
        runs={[run('base', 'merge-base', 0, 2), run('a', 'feat/a', 3, 0), run('b', 'feat/b', 2, 0), run('merge', 'a+b', 3, 2)]}
        {...props}
      />
    );
    const table = screen.getByRole('table', { name: 'Test results per checkout' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('without A' );
    expect(within(rows[0]).getByTestId('tt-base')).toHaveAttribute('data-outcome', 'fail');
    expect(within(rows[0]).getByTestId('tt-b')).toHaveTextContent('feat/bpass2 passed');
    expect(within(rows[1]).getByTestId('tt-a')).toHaveTextContent('feat/apass3 passed');
    expect(within(rows[1]).getByTestId('tt-merge')).toHaveTextContent('A + B mergedfail3 passed, 2 failed');
  });

  it('reports a timeout, an empty run and a missing run honestly', () => {
    render(<TruthTable runs={[run('a', 'feat/a', 0, 0), run('merge', 'a+b', 0, 0, { timed_out: true })]} {...props} />);
    expect(screen.getByTestId('tt-a')).toHaveAttribute('data-outcome', 'none');
    expect(screen.getByTestId('tt-a')).toHaveTextContent('no tests ran');
    expect(screen.getByTestId('tt-merge')).toHaveTextContent('timeout');
    expect(screen.getByTestId('tt-b')).toHaveTextContent('not run');
  });

  it('uses a merge rerun in place of the first merge run', () => {
    render(<TruthTable runs={[run('merge', 'a+b', 3, 0), run('merge-rerun', 'a+b', 1, 2)]} {...props} />);
    expect(screen.getByTestId('tt-merge')).toHaveAttribute('data-outcome', 'fail');
  });

  it('renders nothing without runs', () => {
    const { container } = render(<TruthTable runs={[]} {...props} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('DocsSources', () => {
  it('shows title, host and query for each source', () => {
    render(
      <DocsSources
        sources={[
          { title: 'Validators | Pydantic Docs', url: 'https://docs.pydantic.dev/latest/concepts/validators', query: 'pydantic field_validator' },
        ]}
      />
    );
    const block = screen.getByTestId('docs-sources');
    expect(within(block).getByRole('link', { name: 'Validators | Pydantic Docs' })).toBeInTheDocument();
    expect(block).toHaveTextContent('docs.pydantic.dev');
    expect(within(block).getByText('pydantic field_validator')).toBeInTheDocument();
  });

  it('falls back to the host when a source has no title', () => {
    render(<DocsSources sources={[{ title: '', url: 'https://www.example.com/x', query: '' }]} />);
    expect(screen.getByRole('link', { name: 'example.com' })).toBeInTheDocument();
    expect(screen.queryByText('Query')).toBeNull();
  });

  it('renders nothing when no source is a safe link', () => {
    const { container } = render(<DocsSources sources={[{ title: 'x', url: 'javascript:alert(1)', query: 'q' }]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

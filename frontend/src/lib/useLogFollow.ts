import { useLayoutEffect, useRef, useState } from 'react';

export function isAtBottom(
  scrollHeight: number,
  clientHeight: number,
  scrollTop: number,
): boolean {
  return scrollHeight - clientHeight - scrollTop <= 24;
}

export function useLogFollow(isOpen: boolean, revision: string) {
  const ref = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  useLayoutEffect(() => {
    if (isOpen && following && ref.current)
      ref.current.scrollTop = ref.current.scrollHeight;
  }, [isOpen, following, revision]);
  const onScroll = () => {
    const node = ref.current;
    if (node)
      setFollowing(
        isAtBottom(node.scrollHeight, node.clientHeight, node.scrollTop),
      );
  };
  const jumpToLatest = () => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
    setFollowing(true);
  };
  return { ref, following, onScroll, jumpToLatest };
}

import { useQueryClient } from "@tanstack/react-query";
import {
  useListSolutions,
  useGetSolution,
  useUploadSolution,
  useImportFromGithub,
  useDeleteSolution,
  useGetKnowledgeGraph,
  useGetEntities,
  useGetWorkflows,
  useGetPlugins,
  useGetFunctionalFlows,
  useGenerateDocs,
  useGetDocs,
  useVerifyDocs,
} from "@workspace/api-client-react";

export function useSolutions() {
  return useListSolutions({
    query: {
      refetchInterval: 10000,
    }
  });
}

export function useSolutionDetails(id: string) {
  return useGetSolution(id, {
    query: {
      enabled: !!id,
      refetchInterval: (query) => {
        const status = query.state.data?.status;
        if (status === 'ready' || status === 'error') return false;
        return 5000;
      }
    }
  });
}

export function useUpload() {
  const queryClient = useQueryClient();
  return useUploadSolution({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["/api/py-api/solutions"] });
      },
    },
  });
}

export function useGitHubImport() {
  const queryClient = useQueryClient();
  return useImportFromGithub({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["/api/py-api/solutions"] });
      },
    },
  });
}

export function useDelete() {
  const queryClient = useQueryClient();
  return useDeleteSolution({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: ["/api/py-api/solutions"] });
        queryClient.removeQueries({ queryKey: [`/api/py-api/solutions/${variables.id}`] });
      },
    },
  });
}

export function useKnowledgeGraph(id: string, enabled: boolean = true) {
  return useGetKnowledgeGraph(id, { query: { enabled: !!id && enabled } });
}

export function useEntities(id: string, enabled: boolean = true) {
  return useGetEntities(id, { query: { enabled: !!id && enabled } });
}

export function useWorkflows(id: string, enabled: boolean = true) {
  return useGetWorkflows(id, { query: { enabled: !!id && enabled } });
}

export function usePlugins(id: string, enabled: boolean = true) {
  return useGetPlugins(id, { query: { enabled: !!id && enabled } });
}

export function useFunctionalFlows(id: string, enabled: boolean = true) {
  return useGetFunctionalFlows(id, { query: { enabled: !!id && enabled } });
}

export function useDocs(id: string, enabled: boolean = true) {
  return useGetDocs(id, { query: { enabled: !!id && enabled, retry: false } });
}

export function useGenerateDocumentation() {
  const queryClient = useQueryClient();
  return useGenerateDocs({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: [`/api/py-api/solutions/${variables.id}/docs`] });
        queryClient.invalidateQueries({ queryKey: [`/api/py-api/solutions/${variables.id}`] });
        queryClient.invalidateQueries({ queryKey: ["/api/py-api/solutions"] });
      },
    },
  });
}

export function useVerifyDocumentation() {
  const queryClient = useQueryClient();
  return useVerifyDocs({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: [`/api/py-api/solutions/${variables.id}/docs`] });
      },
    },
  });
}

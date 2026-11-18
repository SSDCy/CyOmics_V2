"""Read-only saved-input composition; no HTTP, pool creation or authorization.

Only the existing authenticated server caller supplies subject. Existing server/
worker factories inject the actual Owner repositories; their methods enforce
Owner/RLS and their original errors propagate. Selection contains no Owner/path.
No configured dependencies means off before imports or I/O.
"""
from dataclasses import dataclass


@dataclass(frozen=True,slots=True)
class Selection:
    processed_dataset_version_id:str
    sample_design_version_id:str
    sample_design_request_id:str
    contrast:dict # Original explicit contrast_design payload, never rewritten.
    replicate_model:str # Original binder's explicit independent_biological choice.


class SavedInputAssembly:
    def __init__(self,preprocessing=None,imports=None,samples=None):
        self.preprocessing=preprocessing;self.imports=imports;self.samples=samples

    async def assemble(self,subject,project,selection=None,budget=None):
        if selection is None or any(repo is None for repo in (self.preprocessing,self.imports,self.samples)):
            return None
        from .preprocess_repository import PreprocessRepository,StoredArtifact
        from .import_repository import ImportRepository,SavedDatasetVersion
        from .sample_repository import SampleDesignRepository
        from .settings import Fault,Budget
        from . import preprocess_contract as pc,quant_diff_input as binding
        if (type(self.preprocessing) is not PreprocessRepository or type(self.imports) is not ImportRepository
                or type(self.samples) is not SampleDesignRepository or type(budget) is not Budget):
            raise Fault(503,'quant_diff_input_unavailable')
        if type(selection) is not Selection:
            raise Fault(400,'invalid_quant_diff_input_selection')
        PreprocessRepository.ids(subject,project,selection.processed_dataset_version_id,
            selection.sample_design_version_id,selection.sample_design_request_id)
        # No catch/remapping of original Fault/ImportFault, including401/403/404.
        version=await self.preprocessing.read_version(subject,project,selection.processed_dataset_version_id,budget)
        try:pc.version_value(version,project=project,version=selection.processed_dataset_version_id)
        except (ValueError,TypeError,KeyError):raise Fault(503,'quant_diff_input_persisted_invalid') from None
        parent_id=version['parent_dataset_version_id'];family=version['quant_family'];source=version['source']
        saved=await self.imports.read_version(subject,project,version['file_id'],parent_id,budget)
        if type(saved) is not SavedDatasetVersion:
            raise Fault(503,'quant_diff_input_persisted_invalid')
        parent=saved.decoded['datasetVersion'];report=saved.decoded['importReport']
        if (parent['dataset_version_id']!=parent_id or parent['project_id']!=project or parent['file_id']!=version['file_id']
                or parent['parser_contract']!=source['parser_contract'] or parent['config_sha256']!=source['config_sha256']
                or parent['source']['project_id']!=project or parent['source']['raw_file_id']!=version['file_id']
                or parent['source']['expected_bytes']!=source['source_bytes']
                or parent['source']['expected_raw_sha256']!=source['source_sha256']
                or saved.report_sha256!=source['report_sha256']
                or report['report']['row_count']!=source['row_count']
                or report['report']['column_count']!=source['column_count']):
            raise Fault(503,'quant_diff_input_source_mismatch')
        selected=[column for column in report['sample_columns'] if column['quant_family']==family]
        if not selected or pc.digest(selected)!=source['selected_columns_sha256']:
            raise Fault(503,'quant_diff_input_source_mismatch')
        # Explicit request lookup only. No latest head or implicit confirmation.
        design=await self.samples.read(subject,project,parent_id,family,budget,
            request_id=selection.sample_design_request_id)
        record=design['record']
        if (record is None or record['sample_design_version_id']!=selection.sample_design_version_id
                or record['request_id']!=selection.sample_design_request_id):
            raise Fault(404,'sample_design_not_found')
        artifacts={}
        for name in pc.ARTIFACT_NAMES:
            artifact=await self.preprocessing.read_artifact(subject,project,selection.processed_dataset_version_id,name,budget)
            if type(artifact) is not StoredArtifact or artifact.name!=name:
                raise Fault(503,'quant_diff_input_persisted_invalid')
            artifacts[name]=artifact.body
        budget.check()
        result=binding.bind(binding.ServerInput(project,selection.processed_dataset_version_id,version,artifacts,
            tuple(report['sample_columns']),design,selection.contrast,selection.replicate_model))
        budget.check()
        return result

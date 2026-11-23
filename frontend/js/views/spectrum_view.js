export const spectrumViewTemplate = `
<section class="panel">
  <h2>温谱导出</h2>
  <p class="hint">仅已封批且当代正式快照清单齐套的批次可导出；同代重复导出将被拒绝。</p>
  <div class="form-row">
    <div><label>已封批批次</label>
      <select v-model.number="spectrumForm.batch_id">
        <option v-for="b in sealedBatches" :key="b.id" :value="b.id">
          {{ b.batch_code }} G{{ b.generation }} · {{ b.manifest_complete ? "清单齐套" : "清单空档" }}
        </option>
      </select>
    </div>
  </div>
  <div class="actions"><button @click="doSpectrum">导出温谱</button></div>
  <table class="data-table">
    <thead><tr><th>导出号</th><th>批次</th><th>代</th><th>点数</th><th>校验</th><th>状态</th></tr></thead>
    <tbody>
      <tr v-for="x in spectrumExports" :key="x.id">
        <td>{{ x.export_ref }}</td>
        <td>{{ x.batch_code }}</td>
        <td>{{ x.export_ref && x.export_ref.includes("G") ? x.export_ref.split("G").pop() : "—" }}</td>
        <td>{{ x.point_count }}</td>
        <td class="mono">{{ x.checksum }}</td>
        <td>{{ x.status }}</td>
      </tr>
    </tbody>
  </table>
  <div class="detail-box">
    <h3>封批代 × 清单齐套</h3>
    <p class="hint">温谱按封批代导出；代已升而清单空档的批次不得亮齐、不得导出。</p>
    <table class="data-table">
      <thead><tr><th>批次</th><th>代</th><th>正式清单行</th><th>口径</th></tr></thead>
      <tbody>
        <tr v-for="b in sealedBatches" :key="'sp-gap-'+b.id">
          <td>{{ b.batch_code }}</td>
          <td>G{{ b.generation }}</td>
          <td>{{ b.seal_snapshot_count || 0 }}</td>
          <td><span class="badge" :class="b.manifest_complete ? 'pass' : 'fail'">{{ b.manifest_complete ? "齐套" : "清单空档" }}</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</section>
`;

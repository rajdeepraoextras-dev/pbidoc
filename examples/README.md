# Examples

Drop sample Power BI exports here to try PBICompass end-to-end:

```bash
# a zipped .pbip project
PYTHONPATH=src python -m pbicompass generate examples/Corporate_Spend_Report.zip -o report.html --bundle
```

> **Note:** `*.zip` files in this folder are git-ignored — they may wrap a
> `.pbip` containing real metadata, and PBICompass never commits customer
> artifacts. The committed, synthetic fixture lives under
> [`tests/fixtures/SampleSales/`](../tests/fixtures/SampleSales/) instead.
</content>
</invoke>

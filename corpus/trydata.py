from trident import *

ds=load_examples_data('chinese')

data=list(ds.testdata.data.items)
label=[t for t in ds.testdata.label.items[0]]







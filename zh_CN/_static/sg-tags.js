// Author: Stuart Mumford
// License: 3-clause BSD
// This code is used on gallery index pages to extract and display a
// list of tags for all examples on that page, and allow the user to
// filter by one or more tags.

// Create a subclass of Set where modifications trigger updates to the page.
class TagSet extends Set {
    // Update the URL to include currently selected tags
    updateURLTags() {
        const searchParams = new URLSearchParams(window.location.search);
        if (this.size > 0) {
            const tagStr = Array.from(this).join(",")
            searchParams.set("sg-tags", tagStr);
        } else {
            searchParams.delete("sg-tags");
        }

        // Update the URL
        const newUrl = `${window.location.pathname}?${searchParams.toString()}`;
        window.history.replaceState({}, "", newUrl);
    }

    // Set all the elements of the list of tags to reflect the currently active tags
    updateTagButtons() {
        const tags = document.querySelectorAll('.sphx-glr-tag');
        tags.forEach(tagElement => {
            tagElement.classList.toggle(
                'active',
                this.has(tagElement.textContent)
            );
        });
    }

    // Set state of clear button
    updateClearButton() {
        const clearElement = document.getElementById('sphx-glr-tag-clear')
        if (this.size > 0) {
            clearElement.style.display = 'block';
        } else {
            clearElement.style.display = 'none';
        }
    }

    // Filter grid items based on selected tags
    filterGrid() {
        const gridItems = document.querySelectorAll('.sphx-glr-thumbcontainer');

        gridItems.forEach(item => {
            let itemTags = new Set();
            if (item.dataset.sgtags) {
                itemTags = new Set(JSON.parse(item.dataset.sgtags));
            }
            const matchesAllSelected = [...this].every(tag => itemTags.has(tag));
            item.style.display = matchesAllSelected || this.size === 0 ? 'block' : 'none';
        });
    }

    updateUI() {
        // Update UI and filter grid
        this.updateTagButtons();
        this.updateClearButton()
        this.updateURLTags();
        this.filterGrid();
    }

    add(value) {
        const result = super.add(value);
        this.updateUI();
        return result;
    }

    delete(value) {
        const result = super.delete(value);
        this.updateUI();
        return result;
    }

    clear() {
        const result = super.clear();
        this.updateUI();
        return result;
    }
}

// Track currently selected tags
let selectedTags = new TagSet();

// Extract all unique tags and populate the tag list
function extractTags() {
    const gridItems = document.querySelectorAll('[data-sgtags]');
    const allTags = new Set();

    gridItems.forEach(item => {
        if (item.dataset.sgtags) {
            const tags = JSON.parse(item.dataset.sgtags);
            tags.forEach(tag => allTags.add(tag));
        }
    });

    const tagList = document.getElementById('sg-tag-list');
    allTags.forEach(tag => {
        const tagElement = document.createElement('div');
        tagElement.className = 'sphx-glr-tag';
        tagElement.textContent = tag;
        tagElement.addEventListener('click', () => toggleTag(tag));
        tagList.appendChild(tagElement);
    });
    if (allTags.size > 0) {
        nameElement = document.createElement('div');
        nameElement.className = 'sphx-glr-tag-label';
        nameElement.textContent = "🏷 Tags:";
        tagList.prepend(nameElement);

        let clearElement = document.createElement('div');
        clearElement.id = 'sphx-glr-tag-clear';
        clearElement.className = 'sphx-glr-tag-label';
        clearElement.textContent = "Clear";
        clearElement.style.display = 'none';  // Hide the element until there is a selected tag
        clearElement.addEventListener('click', () => selectedTags.clear());
        tagList.appendChild(clearElement);
    }

    // Load any tags from the URL
    const searchParams = new URLSearchParams(window.location.search);
    sgTagsParam = searchParams.get("sg-tags");
    if (sgTagsParam !== null && sgTagsParam !== "") {
        const urlTags = searchParams.get("sg-tags").split(",");
        console.log(urlTags);
        urlTags.forEach(tag => {
            toggleTag(tag);
        });
    }
}

// Toggle a tag's selected state and update the grid
function toggleTag(tag) {
    if (selectedTags.has(tag)) {
        selectedTags.delete(tag);
    } else {
        selectedTags.add(tag);
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    extractTags();
});
